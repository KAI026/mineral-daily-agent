from __future__ import annotations

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from mineral_daily.config import Settings
from mineral_daily.models import ResultStatus
from mineral_daily.net import NetworkPolicy
from mineral_daily.services.news import NewsService, _near_duplicate_title


def _settings() -> Settings:
    return Settings(
        http_timeout_seconds=5,
        pdf_max_bytes=1_000_000,
        article_max_bytes=1_000_000,
        user_agent="tests",
        log_level="INFO",
    )


def _rss(now: datetime) -> bytes:
    recent = format_datetime(now - timedelta(days=1))
    old = format_datetime(now - timedelta(days=40))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Mining Test Feed</title>
      <item><title>Pilbara lithium expansion</title>
        <link>https://example.com/story?utm_source=test</link>
        <description>Verified project update for Pilbara lithium operations.</description>
        <pubDate>{recent}</pubDate></item>
      <item><title>Pilbara old item</title><link>https://example.com/old</link>
        <description>Old lithium item.</description><pubDate>{old}</pubDate></item>
    </channel></rss>""".encode()


def _asx_html(now: datetime) -> bytes:
    local = (now - timedelta(hours=6)).astimezone(ZoneInfo("Australia/Sydney"))
    return f"""
    <html><table>
      <tr><th>Date</th><th>Price sens.</th><th>Headline</th></tr>
      <tr><td>{local.strftime("%d/%m/%Y %I:%M %p")}</td><td></td>
        <td><a href="/asx/v2/statistics/displayAnnouncement.do?idsId=123">
          Quarterly Activities Report 12 pages 1.2MB
        </a></td></tr>
    </table></html>
    """.encode()


def test_near_duplicate_event_titles_are_detected() -> None:
    assert _near_duplicate_title(
        "BHP offloads Kambalda concentrator to Gold Fields",
        "BHP agrees to sell Kambalda nickel concentrator to Gold Fields",
    )
    assert not _near_duplicate_title(
        "Kambalda concentrator sale completed",
        "Nickel prices rise after global inventory update",
    )


@pytest.mark.asyncio
async def test_search_filters_by_date_and_deduplicates() -> None:
    now = datetime.now(UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "www.asx.com.au":
            return httpx.Response(
                200,
                content=_asx_html(now),
                headers={"content-type": "text/html"},
            )
        return httpx.Response(
            200,
            content=_rss(now),
            headers={"content-type": "application/rss+xml"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NewsService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).search("Pilbara lithium", days=7)

    assert result.status == ResultStatus.OK
    assert len(result.items) == 2
    rss_item = next(item for item in result.items if item.source != "ASX: PLS")
    asx_item = next(item for item in result.items if item.source == "ASX: PLS")
    assert rss_item.url.endswith("utm_source=test")
    assert asx_item.title == "Quarterly Activities Report"
    assert asx_item.url.startswith("https://www.asx.com.au/")
    assert len(result.sources) == 3


@pytest.mark.asyncio
async def test_search_honours_as_of_window() -> None:
    now = datetime.now(UTC)
    historical = now - timedelta(days=20)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=_rss(historical),
            headers={"content-type": "application/rss+xml"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NewsService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).search("lithium", days=7, as_of=historical.date().isoformat())

    assert result.status == ResultStatus.OK
    assert len(result.items) == 1


@pytest.mark.asyncio
async def test_search_requires_project_identity_not_only_company_name() -> None:
    now = datetime.now(UTC)
    recent = format_datetime(now - timedelta(days=1))
    rss = f"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel><title>Mining Test Feed</title>
      <item><title>Shandong Gold reports higher profit</title>
        <link>https://example.com/company</link>
        <description>Company-wide gold market update without a project reference.</description>
        <pubDate>{recent}</pubDate></item>
      <item><title>Shandong Gold updates Sanshandao mine</title>
        <link>https://example.com/sanshandao</link>
        <description>Operational update for the Sanshandao gold mine.</description>
        <pubDate>{recent}</pubDate></item>
    </channel></rss>""".encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=rss,
            headers={"content-type": "application/rss+xml"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NewsService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).search("Shandong Gold Sanshandao gold mine", days=7)

    assert {item.url for item in result.items} == {"https://example.com/sanshandao"}


@pytest.mark.asyncio
async def test_fetch_article_extracts_readable_body() -> None:
    html = b"""
    <html><head><title>Mine update</title></head><body><nav>Ignore</nav><article>
      <p>Pilbara Minerals published a material operational update with enough detail.</p>
      <p>The second paragraph provides additional production context for readers.</p>
    </article></body></html>
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=html, headers={"content-type": "text/html"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await NewsService(
            settings=_settings(),
            client=client,
            network_policy=NetworkPolicy(resolve_dns=False),
        ).fetch_article("https://example.com/story")

    assert result.status == ResultStatus.OK
    assert result.title == "Mine update"
    assert "operational update" in result.text
    assert "Ignore" not in result.text
