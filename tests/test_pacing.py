import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from headroom_rl.bucket import RateLimitState, TokenBucket
from headroom_rl.config import HeadroomConfig, HeadroomError, PacingMode
from headroom_rl.transport import (
    HeadroomTransport,
    _gate,
    _ModeState,
    _pacing_delay,
    _WindowLearner,
)
from helpers import parse_nasa_headers


def _bucket_with(remaining: int, limit: int = 10, reset_at=None) -> TokenBucket:
    bucket = TokenBucket()
    bucket.reconcile(RateLimitState(limit=limit, remaining=remaining, reset_at=reset_at))
    return bucket


def test_burst_mode_never_paces() -> None:
    bucket = _bucket_with(remaining=1, limit=10)
    config = HeadroomConfig(mode=PacingMode.BURST)
    state = _ModeState()
    state.last_request_at = time.monotonic()
    assert _pacing_delay(bucket, config, state) == 0.0


def test_balance_mode_falls_back_to_burst_without_reset_time() -> None:
    bucket = _bucket_with(remaining=5, limit=10, reset_at=None)
    config = HeadroomConfig(mode=PacingMode.BALANCE)
    state = _ModeState()
    state.last_request_at = time.monotonic()
    assert _pacing_delay(bucket, config, state) == 0.0


def test_balance_mode_falls_back_to_burst_on_first_request() -> None:
    reset_at = datetime.now(timezone.utc) + timedelta(hours=1)
    bucket = _bucket_with(remaining=5, limit=10, reset_at=reset_at)
    config = HeadroomConfig(mode=PacingMode.BALANCE)
    state = _ModeState()  # last_request_at still None — no reference point yet
    assert _pacing_delay(bucket, config, state) == 0.0


def test_balance_mode_spaces_requests_across_remaining_window() -> None:
    reset_at = datetime.now(timezone.utc) + timedelta(seconds=100)
    bucket = _bucket_with(remaining=5, limit=10, reset_at=reset_at)  # 20s/request interval
    config = HeadroomConfig(mode=PacingMode.BALANCE)
    state = _ModeState()
    state.last_request_at = time.monotonic()  # just sent one

    delay = _pacing_delay(bucket, config, state)
    assert 19.0 <= delay <= 20.0


def test_balance_mode_allows_immediately_once_interval_has_passed() -> None:
    reset_at = datetime.now(timezone.utc) + timedelta(seconds=100)
    bucket = _bucket_with(remaining=5, limit=10, reset_at=reset_at)
    config = HeadroomConfig(mode=PacingMode.BALANCE)
    state = _ModeState()
    state.last_request_at = time.monotonic() - 30  # interval (20s) already elapsed

    assert _pacing_delay(bucket, config, state) == 0.0


def test_balance_mode_uses_learned_reset_when_provider_gives_none() -> None:
    bucket = _bucket_with(remaining=5, limit=10, reset_at=None)
    config = HeadroomConfig(mode=PacingMode.BALANCE)
    state = _ModeState()
    state.last_request_at = time.monotonic()
    # simulate a learner that already knows the window is ~100s and a
    # reset was observed 0s ago (monotonic 'now' below), same as what
    # _reconcile would have set up from real observed responses
    state.learner._last_reset_at = time.monotonic()
    state.learner.learned_window_seconds = 100.0

    delay = _pacing_delay(bucket, config, state)
    assert 19.0 <= delay <= 20.0  # same math as the explicit-reset_at case above


def test_gradual_mode_no_delay_above_threshold() -> None:
    bucket = _bucket_with(remaining=8, limit=10)  # 80% remaining, threshold default 30%
    config = HeadroomConfig(mode=PacingMode.GRADUAL)
    state = _ModeState()
    assert _pacing_delay(bucket, config, state) == 0.0


def test_gradual_mode_delay_increases_as_budget_drops() -> None:
    config = HeadroomConfig(mode=PacingMode.GRADUAL, pressure_threshold=0.5, pressure_max_delay=2.0)
    state = _ModeState()

    at_threshold = _pacing_delay(_bucket_with(remaining=5, limit=10), config, state)
    quarter = _pacing_delay(_bucket_with(remaining=3, limit=10), config, state)
    nearly_empty = _pacing_delay(_bucket_with(remaining=1, limit=10), config, state)

    assert at_threshold == 0.0
    assert 0.0 < quarter < nearly_empty <= 2.0


def test_gradual_mode_end_to_end_through_transport() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "1"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(mode=PacingMode.GRADUAL, pressure_threshold=0.5, pressure_max_delay=0.2)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")  # cold start, sets remaining=1/10

    start = time.monotonic()
    client.get("https://api.nasa.gov/planetary/apod")  # remaining well below threshold now
    elapsed = time.monotonic() - start

    assert elapsed >= 0.15  # close to pressure_max_delay (0.2s), allowing scheduling slack


def test_pacing_delay_exceeding_max_wait_raises() -> None:
    reset_at = datetime.now(timezone.utc) + timedelta(seconds=100)
    bucket = _bucket_with(remaining=1, limit=10, reset_at=reset_at)  # 100s/request interval
    config = HeadroomConfig(mode=PacingMode.BALANCE, max_wait=1.0)
    state = _ModeState()
    state.last_request_at = time.monotonic()

    delay = _pacing_delay(bucket, config, state)
    assert delay > config.max_wait  # sanity check on the fixture itself

    with pytest.raises(HeadroomError, match="exceeds max_wait"):
        _gate(bucket, config, state)


# --- _WindowLearner ---------------------------------------------------


def test_learner_needs_two_resets_before_it_learns_anything() -> None:
    learner = _WindowLearner()
    learner.observe(remaining=0, now=0.0)  # baseline, not a reset
    assert learner.learned_window_seconds is None
    learner.observe(remaining=10, now=100.0)  # first reset observed
    assert learner.learned_window_seconds is None  # no prior reset to measure from yet


def test_learner_measures_interval_between_two_resets() -> None:
    learner = _WindowLearner()
    learner.observe(remaining=0, now=0.0)
    learner.observe(remaining=10, now=100.0)  # reset #1
    learner.observe(remaining=0, now=150.0)  # depleting again
    learner.observe(remaining=10, now=200.0)  # reset #2 — interval = 200-100
    assert learner.learned_window_seconds == pytest.approx(100.0)


def test_learner_ignores_non_reset_fluctuation() -> None:
    learner = _WindowLearner()
    learner.observe(remaining=5, now=0.0)
    learner.observe(remaining=4, now=1.0)  # decreasing, not a reset
    assert learner.learned_window_seconds is None


def test_learner_seconds_until_reset_counts_down() -> None:
    learner = _WindowLearner()
    learner.observe(remaining=0, now=0.0)
    learner.observe(remaining=10, now=100.0)
    learner.observe(remaining=0, now=150.0)
    learner.observe(remaining=10, now=200.0)  # learned_window_seconds = 100

    assert learner.seconds_until_reset(now=200.0) == pytest.approx(100.0)
    assert learner.seconds_until_reset(now=250.0) == pytest.approx(50.0)
    assert learner.seconds_until_reset(now=400.0) == 0.0  # clamped, never negative


# --- SLIDING_WINDOW -----------------------------------------------------


def test_sliding_window_falls_back_to_burst_without_limit_or_window() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "9"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(mode=PacingMode.SLIDING_WINDOW)  # no explicit window config yet
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    # first request: cold start, no limit known yet at all -> allowed
    response = client.get("https://api.nasa.gov/planetary/apod")
    assert response.status_code == 200
    # second: limit is now known (10) but window_seconds still isn't (no
    # reset observed, none configured) -> falls back to plain bucket gate
    response = client.get("https://api.nasa.gov/planetary/apod")
    assert response.status_code == 200


def test_sliding_window_explicit_config_admits_up_to_limit_then_blocks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, headers={"x-ratelimit-limit": "100", "x-ratelimit-remaining": "99"}, content=b"{}"
        )

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(
        mode=PacingMode.SLIDING_WINDOW, window_limit=3, window_seconds=60.0, block=False
    )
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    for _ in range(3):
        assert client.get("https://api.nasa.gov/planetary/apod").status_code == 200

    with pytest.raises(HeadroomError, match="sliding window full"):
        client.get("https://api.nasa.gov/planetary/apod")


def test_sliding_window_still_raises_on_real_exhaustion_before_anything_is_learned() -> None:
    """Documents a real constraint, not a bug: learning needs responses to
    have actually been sent and reconciled. Once the bucket is genuinely
    exhausted with no reset_at known and nothing learned yet, headroom-rl
    still raises rather than guess a wait — same as it always has. In a
    long-running process, the *caller's own* retry loop is what eventually
    lands a request after the real window rolls over, which is what lets
    the learner observe its first reset."""
    bucket = _bucket_with(remaining=0, limit=10, reset_at=None)
    config = HeadroomConfig(mode=PacingMode.SLIDING_WINDOW, block=False)
    state = _ModeState()  # nothing learned yet

    with pytest.raises(HeadroomError, match="reset time unknown"):
        _gate(bucket, config, state)


def test_sliding_window_learns_then_enforces_end_to_end() -> None:
    """Exercises the full pipeline through the real transport (not manual
    state seeding like the test above): drives two real reset cycles with
    real sleeps between calls so the learner has to actually observe them,
    confirms it picks up a window duration, then proves SLIDING_WINDOW
    switches from BURST fallback to real local enforcement once it has.
    remaining never touches 0 during the learning phase (cycles 3,2,1) so
    nothing here is masked by ordinary bucket exhaustion."""
    remaining_cycle = [3, 2, 1]
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        remaining = remaining_cycle[calls["n"] % 3]
        calls["n"] += 1
        return httpx.Response(
            200,
            headers={"x-ratelimit-limit": "3", "x-ratelimit-remaining": str(remaining)},
            content=b"{}",
        )

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(mode=PacingMode.SLIDING_WINDOW, block=False)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    # 7 calls = two full cycles plus one, which is what it takes to observe
    # two reset events (3,2,1 -> [reset]3,2,1 -> [reset]3): the second
    # reset is what lets the learner measure an actual interval.
    for _ in range(7):
        client.get("https://api.nasa.gov/planetary/apod")
        time.sleep(0.1)

    learner = transport._mode_state.learner
    assert learner.learned_window_seconds is not None
    assert learner.learned_window_seconds == pytest.approx(0.3, abs=0.2)

    # let every request sent during the learning phase age out of the
    # (now-known) window before testing enforcement in isolation
    time.sleep(learner.learned_window_seconds + 0.1)

    for _ in range(3):  # window_limit defaults to the provider's limit (3)
        client.get("https://api.nasa.gov/planetary/apod")

    with pytest.raises(HeadroomError, match="sliding window full"):
        client.get("https://api.nasa.gov/planetary/apod")


def test_sliding_window_uses_a_learned_window_once_available() -> None:
    # simulates state as it would be after two real reset cycles were
    # observed over time (see the constraint documented above) — proves
    # SLIDING_WINDOW actually switches over to enforcing its own window
    # once the learner has something to work with, instead of forever
    # falling back to the plain bucket gate.
    bucket = _bucket_with(remaining=50, limit=100, reset_at=None)
    state = _ModeState()
    state.learner.learned_window_seconds = 60.0
    config = HeadroomConfig(mode=PacingMode.SLIDING_WINDOW, window_limit=2, block=False)

    assert _gate(bucket, config, state) == 0.0
    state.sent_at.append(time.monotonic())
    assert _gate(bucket, config, state) == 0.0
    state.sent_at.append(time.monotonic())

    with pytest.raises(HeadroomError, match="sliding window full"):
        _gate(bucket, config, state)
