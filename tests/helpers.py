"""Test-only fixtures. headroom-rl ships with no built-in provider parser —
this is the test suite's own copy of a NASA-shaped one (same logic as
examples/providers/nasa_headers.py) so tests have a realistic, non-trivial
parser to exercise the transport against without the package depending on
it."""

from __future__ import annotations

from collections.abc import Mapping

from headroom_rl import RateLimitState

_LIMIT_HEADER = "x-ratelimit-limit"
_REMAINING_HEADER = "x-ratelimit-remaining"


def parse_nasa_headers(headers: Mapping[str, str]) -> RateLimitState | None:
    lower = {k.lower(): v for k, v in headers.items()}
    limit_raw = lower.get(_LIMIT_HEADER)
    remaining_raw = lower.get(_REMAINING_HEADER)
    if limit_raw is None or remaining_raw is None:
        return None
    try:
        limit = int(limit_raw)
        remaining = int(remaining_raw)
    except ValueError:
        return None
    return RateLimitState(limit=limit, remaining=remaining, reset_at=None)
