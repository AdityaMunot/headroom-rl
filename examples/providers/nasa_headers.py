"""Reference provider parser for NASA's Open API (api.nasa.gov) — an
example of what you write to use headroom-rl against a real API, not part
of the package itself. The core library ships with no built-in provider;
`parse_headers` is always something you supply.

NASA sends only these two headers, confirmed against the live DEMO_KEY
response — no reset time, no Retry-After.
"""

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
