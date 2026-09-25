<p align="center"><strong>headroom-rl</strong></p>
<p align="center">Know your limit before you hit it.</p>

<p align="center">
  <a href="https://github.com/AdityaMunot/headroom-rl/actions/workflows/ci.yml"><img src="https://github.com/AdityaMunot/headroom-rl/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python 3.10+">
  <img src="https://img.shields.io/badge/license-MIT-informational" alt="License: MIT">
</p>

---

`headroom-rl` reads the rate-limit headers a provider returns on every response and uses them to stop a request locally, before it is sent, instead of sending it, receiving a 429, and backing off. See [docs/motivation.md](docs/motivation.md) for the reasoning and why it matters beyond your own resource usage.

## Install

```bash
pip install headroom-rl
```

## Quickstart

The library ships with no built-in provider — `parse_headers` turns a provider's response headers into a `RateLimitState` and is always supplied by the caller. See [examples/providers/nasa_headers.py](examples/providers/nasa_headers.py) for a complete reference implementation against NASA's Open API (free, no signup, used throughout this repo's tests and examples), and [examples/providers/README.md](examples/providers/README.md) to contribute one for another API.

```python
import headroom_rl
from providers.nasa_headers import parse_nasa_headers  # your provider's parser

client = headroom_rl.client(parse_headers=parse_nasa_headers)

response = client.get(
    "https://api.nasa.gov/planetary/apod",
    params={"api_key": "DEMO_KEY"},
)
```

`client()` returns a standard `httpx.Client` — use every other `httpx` feature normally. Every call is gated against the locally tracked budget and reconciled from the response headers each time. `async_client()` provides the same interface for `httpx.AsyncClient`.

Runnable end-to-end example, verified against NASA's live API: [examples/nasa_apod.py](examples/nasa_apod.py).

## Handling exhaustion

A call made with no budget left raises `headroom_rl.HeadroomError` — one exception type for every "can't send this within budget" case, with the current remaining/limit/reset state in the message.

```python
try:
    response = client.get(url, params={"api_key": "DEMO_KEY"})
except headroom_rl.HeadroomError as exc:
    print(f"stopped locally, no request sent: {exc}")
```

`ParseHeaders` is also exported (`headroom_rl.ParseHeaders`) if you want to type-hint your own parser function.

## Pacing modes

By default, headroom-rl sends every request immediately until the budget is exhausted (`PacingMode.BURST`). Three other modes change how available budget is spent: `BALANCE` spreads requests evenly across the time remaining until reset, `GRADUAL` adds increasing delay as budget gets low, and `SLIDING_WINDOW` enforces a hard local cap of N requests per trailing window.

```python
from headroom_rl import HeadroomConfig, PacingMode

client = headroom_rl.client(
    parse_headers=parse_nasa_headers,
    config=HeadroomConfig(mode=PacingMode.BALANCE),
)
```

Full explanation, formulas, and the auto-learning mechanism for providers that don't report a reset time: [docs/pacing.md](docs/pacing.md). Runnable side-by-side comparison of all four: `python examples/pacing_modes.py`.

## Seeding a known state

By default, a client starts cold: it has no rate-limit information until the first response arrives, so the first request is always sent unconditionally. If the current state is already known — for example, a long-lived process that persisted it before restarting on the same host — `HeadroomConfig.initial_state` seeds the bucket before any request is sent, through the same reconciliation path a real response would use.

```python
from datetime import datetime, timedelta, timezone
from headroom_rl import HeadroomConfig, RateLimitState

config = HeadroomConfig(
    initial_state=RateLimitState(
        limit=10,
        remaining=4,
        reset_at=datetime.now(timezone.utc) + timedelta(minutes=20),
    ),
)
```

This does not coordinate state across independent processes or instances — it seeds a single local bucket at construction time. Sharing rate-limit state across multiple instances of a running service is a distinct problem, not solved by this feature.

## Advanced: independent buckets, shared connection pool

Calling a provider that throttles per-user rather than globally — for example, a multi-tenant service proxying calls on behalf of many end users, each with their own quota — requires one bucket per user, not one shared across all of them. Since every `client()`/`async_client()` call already builds an independent transport and bucket, constructing one client per user already gives each an independent, correct view of its own quota; no additional feature is required.

To avoid each of those clients opening its own separate connection pool, construct `HeadroomTransport`/`AsyncHeadroomTransport` directly and share one `inner` transport across them:

```python
import httpx
import headroom_rl

shared_pool = httpx.HTTPTransport()

def client_for_user(parse_headers: headroom_rl.ParseHeaders) -> httpx.Client:
    transport = headroom_rl.HeadroomTransport(shared_pool, parse_headers=parse_headers)
    return httpx.Client(transport=transport)
```

Each resulting client keeps its own independent rate-limit bucket while reusing the same underlying connections. This is the escape hatch `client()`'s convenience wrapper doesn't expose — reach for it only when needed, not as a default starting point.

## Documentation

| Doc | Contents |
|---|---|
| [docs/motivation.md](docs/motivation.md) | Why gate locally, and why it matters beyond your own resource usage |
| [docs/pacing.md](docs/pacing.md) | Full reference for all four pacing modes and the auto-learning mechanism |
| [examples/providers/README.md](examples/providers/README.md) | Contributing a provider parser for another API |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [CONTRIBUTING.md](CONTRIBUTING.md) | Branch naming, commit conventions |

## Scope

No built-in provider, no plugin framework, no distributed backend, no retry logic. This is deliberate scope: the core (`bucket.py`, `transport.py`, `config.py`) is provider-agnostic, and NASA's Open API is used throughout this repo's tests and examples as a free, real-header target — not a special case built into the package.

## License

MIT — see [LICENSE](LICENSE).
