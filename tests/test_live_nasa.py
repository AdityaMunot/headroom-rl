"""Opt-in live smoke test against the real NASA API.

Not run by default (see pyproject.toml addopts). Run explicitly with:

    pytest tests/test_live_nasa.py -m live -v

DEMO_KEY is capped at 10 requests/hour — this test intentionally exhausts
it, so don't run it more than once an hour against the same key.
"""

import httpx
import pytest

import headroom_rl
from headroom_rl.config import HeadroomError
from helpers import parse_nasa_headers

APOD_URL = "https://api.nasa.gov/planetary/apod"


@pytest.mark.live
def test_never_hits_a_real_429_before_exhaustion() -> None:
    client = headroom_rl.client(
        parse_headers=parse_nasa_headers, config=headroom_rl.HeadroomConfig(block=False)
    )
    real_429s = 0
    headroom_stops = 0

    for _ in range(15):  # comfortably past the 10/hour cap
        try:
            response = client.get(APOD_URL, params={"api_key": "DEMO_KEY"})
            assert response.status_code == 200
        except HeadroomError:
            headroom_stops += 1
        except httpx.HTTPStatusError:
            real_429s += 1

    assert real_429s == 0
    assert headroom_stops > 0
