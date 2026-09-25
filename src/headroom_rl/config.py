from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .bucket import RateLimitState


class PacingMode(Enum):
    """Controls how already-available budget is spent.

    Independent of exhaustion handling, which is governed by
    `HeadroomConfig.block` and `HeadroomConfig.max_wait` in all modes.
    """

    BURST = "burst"
    """Sends every request immediately until the budget is exhausted.
    Default mode."""

    BALANCE = "balance"
    """Spreads requests evenly across the time remaining until reset, so
    budget is not front-loaded and exhausted before the window ends. Uses
    the provider's reported reset time when available, otherwise a learned
    estimate once one exists (see SLIDING_WINDOW), otherwise falls back to
    BURST."""

    GRADUAL = "gradual"
    """Sends freely while budget is healthy. Once remaining/limit drops
    below `pressure_threshold`, adds increasing delay per request so
    consumption slows gradually instead of stopping abruptly."""

    SLIDING_WINDOW = "sliding_window"
    """Enforces `window_limit` requests per trailing `window_seconds`,
    tracked locally and independent of the provider's own window shape.
    Falls back to BURST until both values are known, whether explicitly
    configured or learned."""


@dataclass(frozen=True)
class HeadroomConfig:
    """Configuration for a `HeadroomTransport` or `client()`/`async_client()`.

    Attributes:
        safety_margin_requests: Reserve this many requests below the
            reported limit before gating begins.
        block: If True, wait for available budget (bounded by `max_wait`)
            when insufficient; if False, raise immediately instead.
        max_wait: Maximum seconds to wait before raising, regardless of
            the reason for waiting. None means no limit.
        mode: Controls how available budget is spent. See `PacingMode`.
        pressure_threshold: GRADUAL mode only. The remaining/limit ratio
            below which delay begins.
        pressure_max_delay: GRADUAL mode only. Delay in seconds applied as
            remaining budget approaches zero.
        window_seconds: SLIDING_WINDOW mode only. Explicit window length.
            None learns the window automatically from observed reset
            events.
        window_limit: SLIDING_WINDOW mode only. Explicit request cap per
            window. None uses the provider's reported limit.
        initial_state: Seeds the bucket with a known rate-limit state at
            construction time, instead of starting cold and waiting for
            the first response. Useful when a caller already knows the
            current state (for example, a long-lived process that
            persisted it before restarting on the same host). Not a
            substitute for coordinating state across independent
            processes or instances — see PLANNING.md.
    """

    safety_margin_requests: int = 0
    block: bool = True
    max_wait: float | None = None
    mode: PacingMode = PacingMode.BURST
    pressure_threshold: float = 0.3
    pressure_max_delay: float = 2.0
    window_seconds: float | None = None
    window_limit: int | None = None
    initial_state: RateLimitState | None = None


class HeadroomError(Exception):
    """Raised when a request cannot be sent within the local rate-limit budget."""
