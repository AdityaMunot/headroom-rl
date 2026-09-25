"""Scenario coverage that NASA's real headers structurally can't exercise
(it never sends a reset time), plus a couple of resilience cases worth
locking in explicitly. Uses a test-only synthetic parser — not shipped in
the package — to simulate a provider that does send a reset time."""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from headroom_rl.bucket import RateLimitState
from headroom_rl.config import HeadroomConfig, HeadroomError
from headroom_rl.transport import HeadroomTransport
from helpers import parse_nasa_headers


def _parse_synthetic_headers(headers: Mapping[str, str]) -> RateLimitState | None:
    lower = {k.lower(): v for k, v in headers.items()}
    limit_raw = lower.get("x-synthetic-limit")
    remaining_raw = lower.get("x-synthetic-remaining")
    reset_after_raw = lower.get("x-synthetic-reset-after")
    if limit_raw is None or remaining_raw is None:
        return None
    try:
        limit = int(limit_raw)
        remaining = int(remaining_raw)
    except ValueError:
        return None
    reset_at = None
    if reset_after_raw is not None:
        reset_at = datetime.now(timezone.utc) + timedelta(seconds=float(reset_after_raw))
    return RateLimitState(limit=limit, remaining=remaining, reset_at=reset_at)


def _synthetic_response(
    remaining: int, limit: int = 5, reset_after: float | None = None
) -> httpx.Response:
    headers = {"x-synthetic-limit": str(limit), "x-synthetic-remaining": str(remaining)}
    if reset_after is not None:
        headers["x-synthetic-reset-after"] = str(reset_after)
    return httpx.Response(200, headers=headers, content=b"{}")


def test_blocks_then_succeeds_when_reset_time_is_known() -> None:
    calls: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(time.monotonic())
        return _synthetic_response(remaining=0, reset_after=0.15)

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=_parse_synthetic_headers)
    client = httpx.Client(transport=transport)

    client.get("https://example.test/thing")  # cold start, sets remaining=0, reset in ~0.15s

    start = time.monotonic()
    client.get("https://example.test/thing")  # should block, then send — not raise
    elapsed = time.monotonic() - start

    assert len(calls) == 2
    assert elapsed >= 0.10  # allow scheduling slack, well below the 0.15s target


def test_raises_when_wait_exceeds_max_wait() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _synthetic_response(remaining=0, reset_after=5.0)

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(max_wait=1.0)
    transport = HeadroomTransport(inner, parse_headers=_parse_synthetic_headers, config=config)
    client = httpx.Client(transport=transport)

    client.get("https://example.test/thing")
    with pytest.raises(HeadroomError, match="exceeds max_wait"):
        client.get("https://example.test/thing")


def test_malformed_headers_mid_sequence_keep_last_good_state() -> None:
    good = httpx.Response(
        200, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "5"}, content=b"{}"
    )
    malformed = httpx.Response(
        200,
        headers={"x-ratelimit-limit": "not-a-number", "x-ratelimit-remaining": "oops"},
        content=b"{}",
    )
    responses = iter([good, malformed])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(responses)

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")  # good headers: remaining -> 5
    client.get("https://api.nasa.gov/planetary/apod")  # malformed: must not corrupt state

    # local decrement from the last *good* reconcile, untouched by the bad response
    assert transport._bucket.snapshot().remaining == 4


def test_429_response_still_reconciles_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "0"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")  # cold start, allowed despite the 429

    assert transport._bucket.snapshot().remaining == 0  # self-corrected from the real response
