from __future__ import annotations

import httpx

from .bucket import RateLimitState
from .config import HeadroomConfig, HeadroomError, PacingMode
from .transport import AsyncHeadroomTransport, HeadroomTransport, ParseHeaders

__all__ = [
    "AsyncHeadroomTransport",
    "HeadroomConfig",
    "HeadroomError",
    "HeadroomTransport",
    "PacingMode",
    "ParseHeaders",
    "RateLimitState",
    "async_client",
    "client",
]


def client(
    *, parse_headers: ParseHeaders, config: HeadroomConfig | None = None, **httpx_kwargs: object
) -> httpx.Client:
    """Builds an `httpx.Client` that gates requests against a provider's
    rate-limit headers.

    Args:
        parse_headers: Parses the provider's response headers. There is no
            built-in provider; this is always supplied by the caller.
        config: Gating and pacing configuration. Defaults to
            `HeadroomConfig()` if omitted.
        **httpx_kwargs: Forwarded to `httpx.Client`.

    Returns:
        A standard `httpx.Client` wired to a `HeadroomTransport`.
    """
    transport = HeadroomTransport(parse_headers=parse_headers, config=config)
    return httpx.Client(transport=transport, **httpx_kwargs)  # type: ignore[arg-type]


def async_client(
    *, parse_headers: ParseHeaders, config: HeadroomConfig | None = None, **httpx_kwargs: object
) -> httpx.AsyncClient:
    """Async counterpart to `client()`. Builds an `httpx.AsyncClient` wired
    to an `AsyncHeadroomTransport`. See `client()` for argument details."""
    transport = AsyncHeadroomTransport(parse_headers=parse_headers, config=config)
    return httpx.AsyncClient(transport=transport, **httpx_kwargs)  # type: ignore[arg-type]
