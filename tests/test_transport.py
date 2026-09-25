import asyncio

import httpx
import pytest

from headroom_rl.config import HeadroomConfig, HeadroomError
from headroom_rl.transport import AsyncHeadroomTransport, HeadroomTransport
from helpers import parse_nasa_headers


def _headers_response(remaining: int, limit: int = 10, content: bytes = b"{}") -> httpx.Response:
    return httpx.Response(
        200,
        headers={"x-ratelimit-limit": str(limit), "x-ratelimit-remaining": str(remaining)},
        content=content,
    )


def test_reconciles_from_response_headers() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _headers_response(remaining=9 - len(calls))

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")
    client.get("https://api.nasa.gov/planetary/apod")

    assert len(calls) == 2
    assert transport._bucket.snapshot().remaining == 7


def test_raises_when_exhausted_with_no_reset_header() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _headers_response(remaining=0)

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")  # cold start, allowed through
    with pytest.raises(HeadroomError):
        client.get("https://api.nasa.gov/planetary/apod")

    assert len(calls) == 1  # second request never reached the inner transport


def test_raise_not_block_when_config_disallows_blocking() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _headers_response(remaining=0)

    inner = httpx.MockTransport(handler)
    config = HeadroomConfig(block=False)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers, config=config)
    client = httpx.Client(transport=transport)

    client.get("https://api.nasa.gov/planetary/apod")
    with pytest.raises(HeadroomError):
        client.get("https://api.nasa.gov/planetary/apod")


class _TrackingStream(httpx.SyncByteStream):
    """Byte stream that records whether anything actually consumed it, so a
    test can prove the transport never read the body on its own."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.read_count = 0

    def __iter__(self):
        self.read_count += 1
        yield from self._chunks

    def close(self) -> None:
        pass


def test_streaming_response_body_not_consumed_early() -> None:
    stream = _TrackingStream([b"hello world"])

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"x-ratelimit-limit": "10", "x-ratelimit-remaining": "5"},
            stream=stream,
        )

    inner = httpx.MockTransport(handler)
    transport = HeadroomTransport(inner, parse_headers=parse_nasa_headers)
    client = httpx.Client(transport=transport)

    with client.stream("GET", "https://api.nasa.gov/planetary/apod") as response:
        assert stream.read_count == 0  # transport's own reconcile step didn't touch the body
        body = b"".join(response.iter_bytes())

    assert body == b"hello world"
    assert stream.read_count == 1  # only the caller's own iteration read it


def test_async_transport_reconciles() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return _headers_response(remaining=9 - len(calls))

    async def run() -> None:
        inner = httpx.MockTransport(handler)
        transport = AsyncHeadroomTransport(inner, parse_headers=parse_nasa_headers)
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.nasa.gov/planetary/apod")
            await client.get("https://api.nasa.gov/planetary/apod")
        assert transport._bucket.snapshot().remaining == 7

    asyncio.run(run())


def test_async_transport_raises_when_exhausted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return _headers_response(remaining=0)

    async def run() -> None:
        inner = httpx.MockTransport(handler)
        transport = AsyncHeadroomTransport(inner, parse_headers=parse_nasa_headers)
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://api.nasa.gov/planetary/apod")
            with pytest.raises(HeadroomError):
                await client.get("https://api.nasa.gov/planetary/apod")

    asyncio.run(run())
