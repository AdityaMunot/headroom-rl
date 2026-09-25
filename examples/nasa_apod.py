"""Run this against NASA's real DEMO_KEY (10 requests/hour) to see headroom_rl
stop calls locally before NASA would ever return a 429.

headroom-rl ships with no built-in provider — parse_nasa_headers is what
you write to use it against a real API. See providers/nasa_headers.py for
the reference parser, and providers/README.md for contributing your own.

    python examples/nasa_apod.py
"""

import headroom_rl
from providers.nasa_headers import parse_nasa_headers

APOD_URL = "https://api.nasa.gov/planetary/apod"


def main() -> None:
    client = headroom_rl.client(
        parse_headers=parse_nasa_headers, config=headroom_rl.HeadroomConfig(block=False)
    )

    for i in range(1, 16):
        try:
            response = client.get(APOD_URL, params={"api_key": "DEMO_KEY"})
            response.raise_for_status()
            print(f"[{i}] ok — {response.json()['title']}")
        except headroom_rl.HeadroomError as exc:
            print(f"[{i}] headroom stopped this call locally: {exc}")
            break


if __name__ == "__main__":
    main()
