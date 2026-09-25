# Provider parsers

Reference implementations of `parse_headers` for real APIs. None of this is part of the installed `headroom-rl` package — the package itself ships zero provider-specific code. These are copy-paste-and-adapt starting points, not an importable plugin system.

## Contributing one

1. Copy the shape of `nasa_headers.py`: one file, one function, no shared base class or interface.

   ```python
   from collections.abc import Mapping
   from headroom_rl import RateLimitState

   def parse_<provider>_headers(headers: Mapping[str, str]) -> RateLimitState | None:
       ...
   ```

2. Name the file `<provider>_headers.py` and the function `parse_<provider>_headers`.
3. Return `None` when the response carries no rate-limit information (a non-throttled endpoint, headers missing or malformed) — never raise, and never guess a value that wasn't actually in the response.
4. Cite where the header names and behavior came from in a module docstring — official docs, or a live response you captured yourself. State plainly whether the provider reports a reset time; if it doesn't, say so (`reset_at=None` is correct and expected for some providers, as it is for NASA).
5. Keep header-name lookups case-insensitive (see `nasa_headers.py` for the pattern) — header casing isn't reliable.
6. Do not touch anything under `src/headroom_rl/`. A provider parser is application code, not library code, even when it lives here as reference material.
