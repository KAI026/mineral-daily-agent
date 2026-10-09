from __future__ import annotations

import httpx
import pytest

from mineral_daily.net import (
    NetworkPolicy,
    OutboundRequestError,
    download_limited,
    validate_remote_url,
)


@pytest.mark.asyncio
async def test_private_addresses_are_rejected() -> None:
    with pytest.raises(OutboundRequestError, match="private"):
        await validate_remote_url("https://127.0.0.1/report.pdf")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/file",
        "https://user:secret@example.com/file",
        "https://localhost/file",
    ],
)
async def test_unsafe_urls_are_rejected(url: str) -> None:
    with pytest.raises(OutboundRequestError):
        await validate_remote_url(url, NetworkPolicy(resolve_dns=False))


@pytest.mark.asyncio
async def test_redirect_targets_are_revalidated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "https://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OutboundRequestError, match="private"):
            await download_limited(
                client,
                "https://example.com/start",
                max_bytes=100,
                policy=NetworkPolicy(resolve_dns=False),
            )


@pytest.mark.asyncio
async def test_streaming_size_limit_is_enforced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"x" * 101, headers={"content-type": "text/plain"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OutboundRequestError, match="exceeds"):
            await download_limited(
                client,
                "https://example.com/data",
                max_bytes=100,
                accepted_content_types=("text",),
                policy=NetworkPolicy(resolve_dns=False),
            )


@pytest.mark.asyncio
async def test_unexpected_content_type_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"{}", headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OutboundRequestError, match="unexpected content type"):
            await download_limited(
                client,
                "https://example.com/data",
                max_bytes=100,
                accepted_content_types=("text",),
                policy=NetworkPolicy(resolve_dns=False),
            )


@pytest.mark.asyncio
async def test_redirect_without_location_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(OutboundRequestError, match="Location"):
            await download_limited(
                client,
                "https://example.com/start",
                max_bytes=100,
                policy=NetworkPolicy(resolve_dns=False),
            )
