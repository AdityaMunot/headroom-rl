from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from datetime import datetime, timezone


@dataclass(frozen=True)
class RateLimitState:
    """Snapshot of a provider's reported rate-limit state.

    Attributes:
        limit: Maximum requests allowed in the current window.
        remaining: Requests still available in the current window.
        reset_at: When the current window resets, if the provider reports it.
        resource: Optional resource or route this state applies to.
    """

    limit: int
    remaining: int
    reset_at: datetime | None
    resource: str | None = None


class TokenBucket:
    """Tracks locally available request budget, reconciled from a
    provider's reported rate-limit state.

    The bucket holds no independent refill logic; its only source of truth
    is the most recent `reconcile()` call. Before the first reconcile, it
    is in a cold-start state and permits every request, since no
    rate-limit information has been observed yet.
    """

    def __init__(self) -> None:
        self._state: RateLimitState | None = None
        self._lock = threading.Lock()

    def try_acquire(self, cost: int = 1, safety_margin: int = 0) -> bool:
        """Reserves `cost` units of budget if available.

        Returns True and decrements the local budget when at least
        `cost + safety_margin` units remain. Returns False otherwise.
        Always returns True before the first `reconcile()` call.
        """
        with self._lock:
            if self._state is None:
                return True
            if self._state.remaining - safety_margin >= cost:
                self._state = replace(self._state, remaining=self._state.remaining - cost)
                return True
            return False

    def time_until_available(self, cost: int = 1, safety_margin: int = 0) -> float | None:
        """Returns seconds until `cost` units would become available.

        Returns 0.0 if the budget is already sufficient, or None if the
        wait cannot be computed because no reset time is known.
        """
        with self._lock:
            if self._state is None:
                return 0.0
            if self._state.remaining - safety_margin >= cost:
                return 0.0
            if self._state.reset_at is None:
                return None
            delta = (self._state.reset_at - datetime.now(timezone.utc)).total_seconds()
            return max(delta, 0.0)

    def reconcile(self, state: RateLimitState) -> None:
        """Updates local state from a provider's reported rate-limit state.

        Rejects a state that is provably older than the current one, based
        on `reset_at`, which guards against an out-of-order response from a
        concurrent request overwriting fresher data.
        """
        with self._lock:
            current = self._state
            if (
                current is None
                or current.reset_at is None
                or state.reset_at is None
                or state.reset_at >= current.reset_at
            ):
                self._state = state

    def snapshot(self) -> RateLimitState | None:
        """Returns the current state, or None before the first reconcile."""
        with self._lock:
            return self._state
