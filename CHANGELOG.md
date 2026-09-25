# Changelog

## [0.1.0] - Unreleased

Initial feature set.

- `headroom_rl.client()` / `async_client()` — httpx clients that gate requests against a provider's rate-limit headers and reconcile state from every response. No built-in provider; `parse_headers` is required.
- `PacingMode` — `BURST` (default), `BALANCE` (spread requests evenly across the time until reset), `GRADUAL` (increasing delay per request as budget gets low), `SLIDING_WINDOW` (local cap of N requests per trailing window). Selected per client via `HeadroomConfig(mode=...)`. See [docs/pacing.md](docs/pacing.md).
- Auto-learning of a provider's window duration (`BALANCE`/`SLIDING_WINDOW`) for providers that don't report a reset time.
- `HeadroomConfig.initial_state` — seeds the bucket with a known `RateLimitState` at construction time instead of starting cold. Not a mechanism for sharing state across processes or instances. See [README.md](README.md#seeding-a-known-state).
- `HeadroomTransport` / `AsyncHeadroomTransport` — exported at the package top level for advanced use (e.g. sharing one connection pool across several independently-throttled clients). `client()`/`async_client()` remain the recommended default.
- `examples/providers/nasa_headers.py`, `examples/nasa_apod.py`, `examples/pacing_modes.py` — reference provider parser and runnable examples. `examples/providers/` is open to contributed parsers for other APIs; see `examples/providers/README.md`.
