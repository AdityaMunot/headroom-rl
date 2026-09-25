"""Runs all four PacingMode values against a mock provider so you can see
the difference without a live API key. See docs/pacing.md for the full
explanation of each mode.

    python examples/pacing_modes.py

The mock simulates a 6-request budget that resets in 3 seconds — small
numbers, chosen so BALANCE's spacing is actually visible in a few seconds
rather than requiring a real hour-long window.
"""

import time
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone

import httpx

from headroom_rl.bucket import RateLimitState
from headroom_rl.config import HeadroomConfig, HeadroomError, PacingMode
from headroom_rl.transport import HeadroomTransport

LIMIT = 6
WINDOW_SECONDS = 3.0


def parse_synthetic_headers(headers: Mapping[str, str]) -> RateLimitState | None:
    """A provider that *does* send a reset time — unlike NASA's real headers,
    which only include limit/remaining. BALANCE needs this (or a learned
    estimate); GRADUAL doesn't (see docs/pacing.md)."""
    lower = {k.lower(): v for k, v in headers.items()}
    limit_raw = lower.get("x-ratelimit-limit")
    remaining_raw = lower.get("x-ratelimit-remaining")
    reset_raw = lower.get("x-ratelimit-reset")
    if limit_raw is None or remaining_raw is None:
        return None
    reset_at = datetime.fromtimestamp(float(reset_raw), tz=timezone.utc) if reset_raw else None
    return RateLimitState(limit=int(limit_raw), remaining=int(remaining_raw), reset_at=reset_at)


def make_client(mode: PacingMode, **config_kwargs: object) -> httpx.Client:
    window_reset = datetime.now(timezone.utc) + timedelta(seconds=WINDOW_SECONDS)
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        remaining = max(LIMIT - calls["n"], 0)
        return httpx.Response(
            200,
            headers={
                "x-ratelimit-limit": str(LIMIT),
                "x-ratelimit-remaining": str(remaining),
                "x-ratelimit-reset": str(window_reset.timestamp()),
            },
            content=b"{}",
        )

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(mode=mode, block=False, **config_kwargs)  # type: ignore[arg-type]
    transport = HeadroomTransport(inner, parse_headers=parse_synthetic_headers, config=config)
    return httpx.Client(transport=transport)


def run(label: str, client: httpx.Client, attempts: int = 8) -> None:
    print(f"\n--- {label} ---")
    start = time.monotonic()
    for i in range(1, attempts + 1):
        try:
            client.get("https://api.example.com/thing")
            print(f"[{i}] sent at t={time.monotonic() - start:5.2f}s")
        except HeadroomError as exc:
            print(f"[{i}] stopped: {exc}")


def main() -> None:
    run("BURST (default)", make_client(PacingMode.BURST))
    run("BALANCE", make_client(PacingMode.BALANCE))
    run(
        "GRADUAL",
        make_client(PacingMode.GRADUAL, pressure_threshold=0.6, pressure_max_delay=0.8),
    )
    # explicit window_seconds/window_limit here, rather than waiting on the
    # auto-learning path — that needs two real reset cycles observed over
    # actual wall-clock time, not something a quick demo script can show
    # (see docs/pacing.md's "Auto-learning" section for why).
    run(
        "SLIDING_WINDOW (explicit config)",
        make_client(PacingMode.SLIDING_WINDOW, window_seconds=1.5, window_limit=3),
    )


if __name__ == "__main__":
    main()
