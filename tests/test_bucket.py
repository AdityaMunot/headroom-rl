import threading
from datetime import datetime, timedelta, timezone

from headroom_rl.bucket import RateLimitState, TokenBucket


def test_cold_start_allows() -> None:
    bucket = TokenBucket()
    assert bucket.try_acquire() is True
    assert bucket.try_acquire(cost=1000) is True


def test_decrements_locally_after_acquire() -> None:
    bucket = TokenBucket()
    bucket.reconcile(RateLimitState(limit=10, remaining=2, reset_at=None))
    assert bucket.try_acquire() is True
    assert bucket.snapshot().remaining == 1
    assert bucket.try_acquire() is True
    assert bucket.snapshot().remaining == 0
    assert bucket.try_acquire() is False


def test_safety_margin_reserves_a_buffer() -> None:
    bucket = TokenBucket()
    bucket.reconcile(RateLimitState(limit=10, remaining=2, reset_at=None))
    assert bucket.try_acquire(safety_margin=2) is False


def test_reconcile_out_of_order_is_ignored() -> None:
    bucket = TokenBucket()
    now = datetime.now(timezone.utc)
    fresh = RateLimitState(limit=10, remaining=1, reset_at=now + timedelta(hours=1))
    stale = RateLimitState(limit=10, remaining=9, reset_at=now)

    bucket.reconcile(fresh)
    bucket.reconcile(stale)  # arrives later but describes an earlier window

    assert bucket.snapshot() == fresh


def test_reconcile_accepts_next_window() -> None:
    bucket = TokenBucket()
    now = datetime.now(timezone.utc)
    first = RateLimitState(limit=10, remaining=0, reset_at=now)
    second = RateLimitState(limit=10, remaining=10, reset_at=now + timedelta(hours=1))

    bucket.reconcile(first)
    bucket.reconcile(second)

    assert bucket.snapshot() == second


def test_time_until_available_zero_when_capacity_exists() -> None:
    bucket = TokenBucket()
    bucket.reconcile(RateLimitState(limit=10, remaining=5, reset_at=None))
    assert bucket.time_until_available() == 0.0


def test_time_until_available_none_when_reset_unknown() -> None:
    bucket = TokenBucket()
    bucket.reconcile(RateLimitState(limit=10, remaining=0, reset_at=None))
    assert bucket.time_until_available() is None


def test_time_until_available_computed_when_reset_known() -> None:
    bucket = TokenBucket()
    reset_at = datetime.now(timezone.utc) + timedelta(seconds=30)
    bucket.reconcile(RateLimitState(limit=10, remaining=0, reset_at=reset_at))
    wait = bucket.time_until_available()
    assert wait is not None
    assert 29.0 <= wait <= 30.0


def test_concurrent_try_acquire_never_overruns_budget() -> None:
    # Proves the lock actually serializes access: 100 threads race for 20
    # tokens, exactly 20 should win, none double-counted or lost.
    bucket = TokenBucket()
    bucket.reconcile(RateLimitState(limit=20, remaining=20, reset_at=None))

    successes: list[int] = []
    successes_lock = threading.Lock()

    def worker() -> None:
        if bucket.try_acquire():
            with successes_lock:
                successes.append(1)

    threads = [threading.Thread(target=worker) for _ in range(100)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(successes) == 20
    assert bucket.snapshot().remaining == 0
