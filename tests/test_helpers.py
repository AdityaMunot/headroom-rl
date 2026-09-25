"""Validates tests/helpers.py's NASA-shaped fixture parser itself — not
package code (headroom-rl ships no built-in provider), but worth locking
down since the rest of the suite relies on it behaving correctly."""

from helpers import parse_nasa_headers


def test_parses_real_captured_header_shape() -> None:
    # Captured live from api.nasa.gov with DEMO_KEY during design review.
    # NASA sends no reset/Retry-After header at all.
    headers = {"x-ratelimit-limit": "10", "x-ratelimit-remaining": "9"}
    state = parse_nasa_headers(headers)
    assert state is not None
    assert state.limit == 10
    assert state.remaining == 9
    assert state.reset_at is None


def test_is_case_insensitive() -> None:
    headers = {"X-RateLimit-Limit": "10", "X-RateLimit-Remaining": "3"}
    state = parse_nasa_headers(headers)
    assert state is not None
    assert state.remaining == 3


def test_missing_headers_returns_none() -> None:
    assert parse_nasa_headers({}) is None
    assert parse_nasa_headers({"x-ratelimit-limit": "10"}) is None


def test_non_numeric_headers_returns_none() -> None:
    headers = {"x-ratelimit-limit": "not-a-number", "x-ratelimit-remaining": "9"}
    assert parse_nasa_headers(headers) is None
