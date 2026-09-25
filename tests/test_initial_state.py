from datetime import datetime, timedelta, timezone

import httpx
import pytest

from headroom_rl.bucket import RateLimitState
from headroom_rl.config import HeadroomConfig, HeadroomError, PacingMode
from headroom_rl.transport import HeadroomTransport
from helpers import parse_nasa_headers


def test_no_initial_state_starts_cold() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "9"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers)

    assert transport._bucket.snapshot() is None


def test_initial_state_seeds_the_bucket_before_any_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("should not be called")

    inner = httpx.MockTransport(handler)
    seed = RateLimitState(limit=10, remaining=4, reset_at=None)
    config = HeadroomConfig(initial_state=seed)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)

    assert transport._bucket.snapshot() == seed


def test_initial_state_gates_the_very_first_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("blocked request should never reach the inner transport")

    inner = httpx.MockTransport(handler)
    seed = RateLimitState(limit=10, remaining=0, reset_at=None)
    config = HeadroomConfig(initial_state=seed, block=False)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    with pytest.raises(HeadroomError):
        client.get("https://api.nasa.gov/planetary/apod")


def test_initial_state_is_consumed_by_the_first_real_request() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "3"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    seed = RateLimitState(limit=10, remaining=5, reset_at=None)
    config = HeadroomConfig(initial_state=seed)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")

    # seed decremented locally to 4 on acquire, then overwritten by the
    # real response's remaining=3 — same reconcile path as any other update
    assert transport._bucket.snapshot().remaining == 3


def test_stale_seed_is_rejected_by_a_fresher_real_response() -> None:
    now = datetime.now(timezone.utc)
    stale_seed = RateLimitState(limit=10, remaining=9, reset_at=now - timedelta(hours=1))
    fresh_state = RateLimitState(limit=10, remaining=10, reset_at=now + timedelta(hours=1))

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit": "10",
                "x-ratelimit-remaining": "10",
            },
            content=b"{}",
        )

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(initial_state=stale_seed)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)

    # sanity: seed applied as given
    assert transport._bucket.snapshot() == stale_seed

    # a fresher real state (later reset_at) is accepted by reconcile's
    # existing monotonic guard, same as it would be for any two responses
    transport._bucket.reconcile(fresh_state)
    assert transport._bucket.snapshot() == fresh_state


def test_initial_state_seeds_the_learner_as_a_baseline() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "10"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    seed = RateLimitState(limit=10, remaining=2, reset_at=None)
    config = HeadroomConfig(initial_state=seed, mode=PacingMode.SLIDING_WINDOW)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    # remaining goes 2 (seed) -> 10 (real response): a reset relative to
    # the seed baseline, detected on the very first real request instead
    # of needing a second real response to establish that baseline first
    client.get("https://api.nasa.gov/planetary/apod")

    assert transport._mode_state.learner._last_reset_at is not None
