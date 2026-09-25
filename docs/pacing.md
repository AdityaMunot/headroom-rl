# Pacing modes

Pacing controls how already-available budget is spent, not what happens once it's gone — that part (`block`, `max_wait`, and the `HeadroomError` raised when nothing is left) is unaffected by pacing mode; see [README.md](../README.md#handling-exhaustion) for that.

Set the mode per client. Snippets below assume `parse_headers` is a provider parser — see [examples/providers/nasa_headers.py](../examples/providers/nasa_headers.py) for a complete one. There is no built-in provider.

```python
from headroom_rl import HeadroomConfig, PacingMode

client = headroom_rl.client(parse_headers=parse_headers, config=HeadroomConfig(mode=PacingMode.BALANCE))
```

Run `examples/pacing_modes.py` to see all four side by side — it uses a mock provider so it runs instantly and needs no API key.

## At a glance

| Mode | Behavior | Needs reset time? | Config knobs (default) | Good for |
|---|---|---|---|---|
| `BURST` *(default)* | Fires immediately until budget runs out | No | — | Max throughput, fixed batches |
| `BALANCE` | `interval = seconds_until_reset / remaining`; waits between sends | Provider's, or a **learned** estimate — falls back to `BURST` until either exists | — | Steady background/worker traffic |
| `GRADUAL` | Delay ramps `0 → pressure_max_delay` once `remaining/limit` drops below `pressure_threshold` | No | `pressure_threshold=0.3`, `pressure_max_delay=2.0` | Fast most of the time, graceful slowdown near empty |
| `SLIDING_WINDOW` | Enforces `window_limit` requests per trailing `window_seconds`, tracked locally | Explicit config, or **learned** — falls back to `BURST` until either exists | `window_seconds=None`, `window_limit=None` | A hard local cap independent of the provider's own window shape |

Details and formulas for each below.

## `PacingMode.BURST` (default)

Sends every request immediately, as fast as the caller invokes it, until the budget is exhausted — then the standard exhaustion behavior (`block`/`max_wait`/`HeadroomError`) takes over. Default when no mode is specified.

**Use it when:** you want maximum throughput and don't care about consumption shape — a script doing a fixed batch of calls, or a latency-sensitive path where "wait until quota decides to allow you" beats "wait unconditionally."

```python
client = headroom_rl.client(parse_headers=parse_headers)  # BURST is the default, no config needed
```

## `PacingMode.BALANCE`

Spreads requests evenly across the time remaining until reset, instead of exhausting budget early and leaving none for the remainder of the window. On each request it computes:

```
interval = seconds_until_reset / remaining
```

and waits until `interval` seconds have passed since the last request before sending the next one.

`seconds_until_reset` comes from the provider's own reported reset time when there is one. If there isn't — like NASA, whose headers only include `limit`/`remaining` with no reset field at all — it falls back to a **learned** estimate (see "Auto-learning" below) once one is available, and to plain `BURST` behavior before that. This is the same "don't guess what you haven't observed" rule the whole library follows — it just now has one narrow, explicit source of observed information to draw on instead of none.

**Use it when:** a background job or worker should trickle requests steadily across an hour instead of firing 100 in the first minute and then sitting idle for 59.

```python
client = headroom_rl.client(parse_headers=parse_headers, config=HeadroomConfig(mode=PacingMode.BALANCE))
```

## `PacingMode.GRADUAL`

*(Renamed from `SLIDING_PRESSURE`. This mode has no relationship to a time window — the previous name was easily confused with `SLIDING_WINDOW` below.)*

Sends freely while budget is healthy. Once `remaining / limit` drops below `pressure_threshold`, it starts adding delay before each request, scaling up linearly to `pressure_max_delay` as budget approaches zero:

```
ratio = remaining / limit
if ratio >= pressure_threshold:
    delay = 0
else:
    pressure = 1 - (ratio / pressure_threshold)
    delay = pressure * pressure_max_delay
```

Unlike `BALANCE`, this only needs `remaining`/`limit` — **no reset time required, learned or otherwise** — so it works against any provider, including NASA, from the very first response.

Config knobs (both on `HeadroomConfig`):
- `pressure_threshold: float = 0.3` — the remaining/limit ratio below which slowdown starts (default: last 30% of budget)
- `pressure_max_delay: float = 2.0` — the delay in seconds applied right as budget approaches zero

**Use it when:** you want full speed most of the time but a graceful slowdown near the edge instead of bursting flat-out straight into a hard stop.

```python
client = headroom_rl.client(
    parse_headers=parse_headers,
    config=HeadroomConfig(
        mode=PacingMode.GRADUAL,
        pressure_threshold=0.3,
        pressure_max_delay=2.0,
    ),
)
```

## `PacingMode.SLIDING_WINDOW`

An independent sliding-window throttle: headroom-rl maintains its own log of sent-request timestamps and admits a new request only if fewer than `window_limit` were sent within the trailing `window_seconds`. Unlike the other modes, this does not consult the provider's `remaining` count for its admission decision — it is a local policy, enforced independently.

```python
client = headroom_rl.client(
    parse_headers=parse_headers,
    config=HeadroomConfig(
        mode=PacingMode.SLIDING_WINDOW,
        window_seconds=60.0,
        window_limit=10,
    ),
)
```

If you don't set `window_limit`, it defaults to the provider's reported `limit`. If you don't set `window_seconds`, it falls back to a **learned** estimate (below) once one exists, and to plain `BURST` before that — same fallback rule as `BALANCE`.

## Auto-learning

Applies to `BALANCE` and `SLIDING_WINDOW` only, and only when a window duration isn't explicitly configured.

Some providers, NASA included, never report a reset time. Rather than permanently fall back to `BURST` against such a provider, `BALANCE`/`SLIDING_WINDOW` can learn the window duration by observing the one unambiguous signal a provider's `remaining` count gives for free: when it increases instead of continuing to fall, a reset occurred. The interval between two observed resets is the window duration.

This is the one case where headroom-rl derives a value rather than reading it directly from a response — scoped narrowly to a single number, from directly observed behavior. It requires no opt-in: observation always happens, and modes that don't use the result simply don't consult it.

**Constraint:** learning requires a request to actually be sent and its response reconciled — a request that headroom-rl itself blocked cannot be the one that reveals a reset. Consequently, a client already at zero budget with nothing learned yet still raises `HeadroomError` rather than guess a wait, exactly as it always has. What bootstraps learning in practice is the caller's own retry behavior: a call made after the real-world window has rolled over succeeds, and that response is what the learner observes. Two such resets are required — which may span two full cycles (e.g. two hours for an hourly limit) — before `learned_window_seconds` has a value to offer `BALANCE`/`SLIDING_WINDOW`.

## Interaction with `max_wait`

`max_wait` applies to any computed wait, regardless of whether it originated from insufficient budget or from pacing. A `BALANCE`/`GRADUAL`/`SLIDING_WINDOW` delay that would exceed `max_wait` raises `HeadroomError` instead of sleeping past it, the same as the exhaustion case. See `tests/test_pacing.py::test_pacing_delay_exceeding_max_wait_raises`.
