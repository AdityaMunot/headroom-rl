from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from datetime import datetime, timezone

import httpx

from .bucket import RateLimitState, TokenBucket
from .config import HeadroomConfig, HeadroomError, PacingMode

ParseHeaders = Callable[[Mapping[str, str]], "RateLimitState | None"]
"""Parses a provider's response headers into a `RateLimitState`, or returns
None if the response carries no rate-limit information."""


class _WindowLearner:
    """Estimates a provider's rate-limit window duration by observing when
    `remaining` increases, which indicates the window has reset.

    Used when a provider does not report a reset time. Requires two
    observed resets before producing an estimate; until then, callers
    should treat the window duration as unknown.
    """

    def __init__(self) -> None:
        self.learned_window_seconds: float | None = None
        self._last_remaining: int | None = None
        self._last_reset_at: float | None = None  # monotonic clock

    def observe(self, remaining: int, now: float) -> None:
        """Records an observed `remaining` value at monotonic time `now`."""
        if self._last_remaining is not None and remaining > self._last_remaining:
            if self._last_reset_at is not None:
                self.learned_window_seconds = now - self._last_reset_at
            self._last_reset_at = now
        self._last_remaining = remaining

    def seconds_until_reset(self, now: float) -> float | None:
        """Returns the estimated seconds until the next reset, or None if
        no estimate is available yet."""
        if self.learned_window_seconds is None or self._last_reset_at is None:
            return None
        elapsed = now - self._last_reset_at
        return max(self.learned_window_seconds - elapsed, 0.0)


class _ModeState:
    """Mutable per-transport state for pacing modes other than BURST.

    Grouped into a single object so `_gate` and `_reconcile` take a fixed
    number of arguments as pacing modes are added.
    """

    def __init__(self) -> None:
        self.last_request_at: float | None = None
        self.learner = _WindowLearner()
        self.sent_at: list[float] = []  # SLIDING_WINDOW's local admission log


def _unavailable_message(bucket: TokenBucket, reason: str) -> str:
    state = bucket.snapshot()
    if state is None:
        return f"headroom: no capacity information yet ({reason})"
    return (
        f"headroom: insufficient rate-limit budget ({reason}). "
        f"remaining={state.remaining} limit={state.limit} reset_at={state.reset_at}"
    )


def _seconds_until_reset(
    state: RateLimitState, learner: _WindowLearner, now: float
) -> float | None:
    if state.reset_at is not None:
        return max((state.reset_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
    return learner.seconds_until_reset(now)


def _pacing_delay(bucket: TokenBucket, config: HeadroomConfig, mode_state: _ModeState) -> float:
    """Returns the additional delay required by BALANCE or GRADUAL mode,
    on top of a request that already has budget available. Returns 0.0 for
    other modes, and whenever there is not enough information to pace
    (falls back to sending immediately)."""
    if config.mode not in (PacingMode.BALANCE, PacingMode.GRADUAL):
        return 0.0

    state = bucket.snapshot()
    if state is None or state.limit <= 0:
        return 0.0

    now = time.monotonic()

    if config.mode is PacingMode.BALANCE:
        if mode_state.last_request_at is None:
            return 0.0
        seconds_left = _seconds_until_reset(state, mode_state.learner, now)
        if seconds_left is None:
            return 0.0
        interval = seconds_left / max(state.remaining, 1)
        earliest = mode_state.last_request_at + interval
        return max(earliest - now, 0.0)

    # GRADUAL
    ratio = state.remaining / state.limit
    if ratio >= config.pressure_threshold:
        return 0.0
    pressure = 1 - (ratio / config.pressure_threshold)
    return pressure * config.pressure_max_delay


def _gate_sliding_window(
    bucket: TokenBucket, config: HeadroomConfig, mode_state: _ModeState
) -> float | None:
    """Returns the wait required by SLIDING_WINDOW mode, or None if the
    window is not yet known (no explicit configuration and nothing
    learned). Callers should fall back to the regular bucket-based gate
    when None is returned."""
    state = bucket.snapshot()
    limit = config.window_limit or (state.limit if state else None)
    window_seconds = config.window_seconds or mode_state.learner.learned_window_seconds
    if limit is None or window_seconds is None:
        return None

    now = time.monotonic()
    cutoff = now - window_seconds
    mode_state.sent_at = [t for t in mode_state.sent_at if t >= cutoff]

    if len(mode_state.sent_at) < limit:
        return 0.0

    oldest = mode_state.sent_at[0]
    wait = max((oldest + window_seconds) - now, 0.0)
    count = len(mode_state.sent_at)
    if not config.block:
        reason = (
            f"sliding window full ({count}/{limit} in last {window_seconds:.1f}s), "
            "blocking disabled"
        )
        raise HeadroomError(_unavailable_message(bucket, reason))
    if config.max_wait is not None and wait > config.max_wait:
        reason = f"sliding window wait {wait:.1f}s exceeds max_wait {config.max_wait:.1f}s"
        raise HeadroomError(_unavailable_message(bucket, reason))
    return wait


def _gate(bucket: TokenBucket, config: HeadroomConfig, mode_state: _ModeState) -> float:
    """Returns seconds the caller should sleep before sending, or raises
    HeadroomError if the request cannot be sent within budget at all."""
    if config.mode is PacingMode.SLIDING_WINDOW:
        sliding_wait = _gate_sliding_window(bucket, config, mode_state)
        if sliding_wait is not None:
            return sliding_wait
        # Window duration not yet known; fall through to the standard
        # bucket-based gate below, same as BURST.

    if bucket.try_acquire(cost=1, safety_margin=config.safety_margin_requests):
        wait = _pacing_delay(bucket, config, mode_state)
        if config.max_wait is not None and wait > config.max_wait:
            reason = f"pacing delay {wait:.1f}s exceeds max_wait {config.max_wait:.1f}s"
            raise HeadroomError(_unavailable_message(bucket, reason))
        return wait

    maybe_wait = bucket.time_until_available(cost=1, safety_margin=config.safety_margin_requests)
    if maybe_wait is None:
        raise HeadroomError(_unavailable_message(bucket, "reset time unknown, can't wait"))
    wait = maybe_wait
    if not config.block:
        raise HeadroomError(_unavailable_message(bucket, "blocking disabled"))
    if config.max_wait is not None and wait > config.max_wait:
        reason = f"wait {wait:.1f}s exceeds max_wait {config.max_wait:.1f}s"
        raise HeadroomError(_unavailable_message(bucket, reason))
    return wait


def _reconcile(
    bucket: TokenBucket,
    parse_headers: ParseHeaders,
    response: httpx.Response,
    mode_state: _ModeState,
) -> None:
    """Updates bucket and learner state from a response's headers, if the
    parser recognizes them."""
    state = parse_headers(response.headers)
    if state is not None:
        bucket.reconcile(state)
        mode_state.learner.observe(state.remaining, time.monotonic())


def _seed(bucket: TokenBucket, mode_state: _ModeState, config: HeadroomConfig) -> None:
    """Applies `config.initial_state`, if set, through the same reconcile
    and observe path a real response would take."""
    if config.initial_state is not None:
        bucket.reconcile(config.initial_state)
        mode_state.learner.observe(config.initial_state.remaining, time.monotonic())


class HeadroomTransport(httpx.BaseTransport):
    """An httpx transport that gates requests against a provider's
    rate-limit headers and reconciles state from every response.

    Wraps an inner transport (an `httpx.HTTPTransport` by default) so it
    can be used as a drop-in replacement via `httpx.Client(transport=...)`.
    """

    def __init__(
        self,
        inner: httpx.BaseTransport | None = None,
        *,
        parse_headers: ParseHeaders,
        config: HeadroomConfig | None = None,
    ) -> None:
        self._inner = inner if inner is not None else httpx.HTTPTransport()
        self._parse_headers = parse_headers
        self._config = config or HeadroomConfig()
        self._bucket = TokenBucket()
        self._mode_state = _ModeState()
        _seed(self._bucket, self._mode_state, self._config)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        wait = _gate(self._bucket, self._config, self._mode_state)
        if wait:
            time.sleep(wait)
        now = time.monotonic()
        self._mode_state.last_request_at = now
        if self._config.mode is PacingMode.SLIDING_WINDOW:
            self._mode_state.sent_at.append(now)
        response = self._inner.handle_request(request)
        _reconcile(self._bucket, self._parse_headers, response, self._mode_state)
        return response

    def close(self) -> None:
        self._inner.close()


class AsyncHeadroomTransport(httpx.AsyncBaseTransport):
    """Async counterpart to `HeadroomTransport`, for use with `httpx.AsyncClient`."""

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        parse_headers: ParseHeaders,
        config: HeadroomConfig | None = None,
    ) -> None:
        self._inner = inner if inner is not None else httpx.AsyncHTTPTransport()
        self._parse_headers = parse_headers
        self._config = config or HeadroomConfig()
        self._bucket = TokenBucket()
        self._mode_state = _ModeState()
        _seed(self._bucket, self._mode_state, self._config)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        wait = _gate(self._bucket, self._config, self._mode_state)
        if wait:
            await asyncio.sleep(wait)
        now = time.monotonic()
        self._mode_state.last_request_at = now
        if self._config.mode is PacingMode.SLIDING_WINDOW:
            self._mode_state.sent_at.append(now)
        response = await self._inner.handle_async_request(request)
        _reconcile(self._bucket, self._parse_headers, response, self._mode_state)
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()
