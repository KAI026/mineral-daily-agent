from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, quote_plus, urlencode, urljoin, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup

from mineral_daily.config import Settings
from mineral_daily.models import (
    ArticleResult,
    NewsItem,
    NewsSearchResult,
    ResultStatus,
    SourceReference,
)
from mineral_daily.net import NetworkPolicy, OutboundRequestError, download_limited

MINING_FEED = "https://www.mining.com/feed/"
GOOGLE_NEWS_FEED = "https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en"
PLS_ASX_ANNOUNCEMENTS = (
    "https://www.asx.com.au/asx/v2/statistics/announcements.do"
    "?asxCode=PLS&by=asxCode&period=M6&timeframe=D"
)
TRACKING_KEYS = {"gclid", "fbclid", "mc_cid", "mc_eid"}
TITLE_STOP_TERMS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "to",
    "for",
    "of",
    "in",
    "on",
    "as",
    "with",
    "from",
    "at",
    "new",
    "update",
    "reports",
    "mining",
    "mine",
}
ASX_RELEVANT_TITLE_RE = re.compile(
    r"\b(?:activities|annual results|financial results|production|sales|guidance|resource|reserve|"
    r"pilgangoora|lithium|project|expansion|p2000|operational|quarterly)\b",
    re.IGNORECASE,
)


def _canonical_url(value: str) -> str:
    parsed = urlsplit(value)
    query = [
        (key, val)
        for key, val in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.lower().startswith("utm_") and key.lower() not in TRACKING_KEYS
    ]
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/"),
            urlencode(query),
            "",
        )
    )


def _title_tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", value.casefold())
        if len(token) > 2 and token not in TITLE_STOP_TERMS
    }


def _near_duplicate_title(left: str, right: str) -> bool:
    left_tokens = _title_tokens(left)
    right_tokens = _title_tokens(right)
    if not left_tokens or not right_tokens:
        return False
    shared = len(left_tokens & right_tokens)
    return shared >= 3 and shared / min(len(left_tokens), len(right_tokens)) >= 0.4


def _entry_datetime(entry: feedparser.FeedParserDict) -> datetime | None:
    for key in ("published", "updated"):
        value = entry.get(key)
        if not value:
            continue
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except (TypeError, ValueError, OverflowError):
            continue
    return None


def _clean_text(value: str, limit: int = 600) -> str:
    text = BeautifulSoup(value or "", "html.parser").get_text(" ", strip=True)
    return re.sub(r"\s+", " ", text)[:limit]


def parse_asx_announcements(
    content: bytes, base_url: str, cutoff: datetime, window_end: datetime
) -> list[NewsItem]:
    """Parse the public PLS announcement table while preserving ASX source links."""
    soup = BeautifulSoup(content, "html.parser")
    items: list[NewsItem] = []
    local_zone = ZoneInfo("Australia/Sydney")
    for row in soup.select("table tr"):
        cells = row.select("td")
        link = row.select_one("a[href]")
        if len(cells) < 3 or link is None:
            continue
        date_text = re.sub(r"\s+", " ", cells[0].get_text(" ", strip=True))
        try:
            published = datetime.strptime(date_text, "%d/%m/%Y %I:%M %p").replace(tzinfo=local_zone)
        except ValueError:
            continue
        published = published.astimezone(UTC)
        if published < cutoff or published >= window_end:
            continue
        raw_title = re.sub(r"\s+", " ", cells[2].get_text(" ", strip=True))
        title = re.sub(
            r"\s+\d+\s+pages?\s+[\d.]+\s*(?:KB|MB)\s*$", "", raw_title, flags=re.IGNORECASE
        ).strip()
        href = str(link.get("href") or "").strip()
        if not title or not href or not ASX_RELEVANT_TITLE_RE.search(title):
            continue
        items.append(
            NewsItem(
                title=title,
                url=urljoin(base_url, href),
                source="ASX: PLS",
                published_at=published,
                summary="PLS 向澳大利亚证券交易所提交的公司公告。",
            )
        )
    return items


class NewsService:
    def __init__(
        self,
        *,
        settings: Settings | None = None,
        client: httpx.AsyncClient | None = None,
        network_policy: NetworkPolicy | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self._client = client
        self.network_policy = network_policy or NetworkPolicy()

    @asynccontextmanager
    async def _http(self) -> AsyncIterator[httpx.AsyncClient]:
        if self._client is not None:
            yield self._client
            return
        async with httpx.AsyncClient(
            timeout=self.settings.http_timeout_seconds,
            headers={"User-Agent": self.settings.user_agent, "Accept": "*/*"},
        ) as client:
            yield client

    async def _read_feed(self, client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
        downloaded = await download_limited(
            client,
            url,
            max_bytes=2 * 1024 * 1024,
            accepted_content_types=("application/rss+xml", "application/xml", "text/xml", "text"),
            policy=self.network_policy,
        )
        return downloaded.content, downloaded.final_url

    async def _read_html(self, client: httpx.AsyncClient, url: str) -> tuple[bytes, str]:
        downloaded = await download_limited(
            client,
            url,
            max_bytes=2 * 1024 * 1024,
            accepted_content_types=("text/html", "application/xhtml+xml"),
            policy=self.network_policy,
        )
        return downloaded.content, downloaded.final_url

    async def search(
        self, query: str, days: int = 7, limit: int = 10, as_of: str | None = None
    ) -> NewsSearchResult:
        query = query.strip()
        if not query:
            return NewsSearchResult(
                status=ResultStatus.ERROR,
                query=query,
                days=days,
                warnings=["query must not be empty"],
            )
        if not 1 <= days <= 365:
            return NewsSearchResult(
                status=ResultStatus.ERROR,
                query=query,
                days=days,
                warnings=["days must be between 1 and 365"],
            )
        if not 1 <= limit <= 50:
            return NewsSearchResult(
                status=ResultStatus.ERROR,
                query=query,
                days=days,
                warnings=["limit must be between 1 and 50"],
            )
        try:
            as_of_day = date.fromisoformat(as_of) if as_of else None
        except ValueError:
            return NewsSearchResult(
                status=ResultStatus.ERROR,
                query=query,
                days=days,
                warnings=["as_of must use ISO format YYYY-MM-DD"],
            )
        today = datetime.now(UTC).date()
        if as_of_day and as_of_day > today:
            return NewsSearchResult(
                status=ResultStatus.ERROR,
                query=query,
                days=days,
                warnings=["as_of cannot be in the future"],
            )

        sources_to_read: list[tuple[str, str]] = [
            ("rss", MINING_FEED),
            ("rss", GOOGLE_NEWS_FEED.format(query=quote_plus(query + " mining"))),
        ]
        if re.search(r"\b(?:pilbara|pilgangoora|pls)\b|皮尔巴拉", query, re.IGNORECASE):
            sources_to_read.append(("asx", PLS_ASX_ANNOUNCEMENTS))
        warnings: list[str] = []
        sources: list[SourceReference] = []
        items: dict[str, NewsItem] = {}
        window_end = (
            datetime.combine(as_of_day + timedelta(days=1), time.min, tzinfo=UTC)
            if as_of_day
            else datetime.now(UTC)
        )
        cutoff = window_end - timedelta(days=days)
        terms = {term.casefold() for term in re.findall(r"[\w-]+", query) if len(term) > 1}
        generic_terms = {
            "mine",
            "mining",
            "mineral",
            "minerals",
            "gold",
            "copper",
            "lithium",
            "silver",
            "aluminium",
            "aluminum",
            "nickel",
            "uranium",
            "cobalt",
            "rare",
            "earth",
            "iron",
            "ore",
            "platinum",
            "palladium",
            "zinc",
            "lead",
            "tin",
            "manganese",
            "graphite",
            "phosphate",
            "potash",
            "金矿",
            "铜矿",
            "锂矿",
            "银矿",
            "铝矿",
            "镍矿",
            "铀矿",
            "钴矿",
            "稀土矿",
            "铁矿石",
            "铂矿",
            "钯矿",
            "锌矿",
            "铅矿",
            "锡矿",
            "锰矿",
            "石墨矿",
            "磷矿",
            "钾盐矿",
        }
        identity_terms = terms - generic_terms
        required_terms = identity_terms or terms
        minimum_term_matches = 2 if len(required_terms) >= 2 else 1

        async with self._http() as client:
            responses = await asyncio.gather(
                *(
                    self._read_feed(client, url) if kind == "rss" else self._read_html(client, url)
                    for kind, url in sources_to_read
                ),
                return_exceptions=True,
            )
        for (kind, configured_url), response in zip(sources_to_read, responses, strict=True):
            if isinstance(response, BaseException):
                warnings.append(
                    f"feed unavailable ({urlsplit(configured_url).hostname}): {response}"
                )
                continue
            body, final_url = response
            if kind == "asx":
                sources.append(
                    SourceReference(
                        title="PLS ASX company announcements",
                        url=final_url,
                        provider="ASX",
                        retrieved_at=datetime.now(UTC),
                    )
                )
                for item in parse_asx_announcements(body, final_url, cutoff, window_end):
                    items.setdefault(_canonical_url(item.url), item)
                continue
            parsed = feedparser.parse(body)
            provider = parsed.feed.get("title") or urlsplit(final_url).hostname or "RSS"
            sources.append(
                SourceReference(
                    title=str(provider),
                    url=final_url,
                    provider="RSS",
                    retrieved_at=datetime.now(UTC),
                )
            )
            for entry in parsed.entries:
                link = str(entry.get("link", "")).strip()
                title = _clean_text(str(entry.get("title", "")), 300)
                summary = _clean_text(str(entry.get("summary", entry.get("description", ""))))
                published = _entry_datetime(entry)
                if (
                    not link
                    or not title
                    or published is None
                    or published < cutoff
                    or published >= window_end
                ):
                    continue
                haystack = f"{title} {summary}".casefold()
                if required_terms and sum(term in haystack for term in required_terms) < minimum_term_matches:
                    continue
                canonical = _canonical_url(link)
                items.setdefault(
                    canonical,
                    NewsItem(
                        title=title,
                        url=link,
                        source=str(entry.get("source", {}).get("title", provider)),
                        published_at=published,
                        summary=summary,
                    ),
                )

        candidates = sorted(
            items.values(),
            key=lambda item: item.published_at or datetime.min.replace(tzinfo=UTC),
            reverse=True,
        )
        ordered: list[NewsItem] = []
        for candidate in candidates:
            if any(_near_duplicate_title(candidate.title, item.title) for item in ordered):
                continue
            ordered.append(candidate)
            if len(ordered) >= limit:
                break
        if not ordered:
            warnings.append("no verifiable, in-window articles matched the query")
        status = ResultStatus.OK if ordered and not warnings else ResultStatus.PARTIAL
        if not sources:
            status = ResultStatus.ERROR
        return NewsSearchResult(
            status=status,
            query=query,
            days=days,
            items=ordered,
            sources=sources,
            warnings=warnings,
        )

    async def fetch_article(self, url: str, max_chars: int = 12_000) -> ArticleResult:
        if not 500 <= max_chars <= 50_000:
            return ArticleResult(
                status=ResultStatus.ERROR,
                requested_url=url,
                warnings=["max_chars must be between 500 and 50000"],
            )
        try:
            async with self._http() as client:
                downloaded = await download_limited(
                    client,
                    url,
                    max_bytes=self.settings.article_max_bytes,
                    accepted_content_types=("text/html", "application/xhtml+xml"),
                    policy=self.network_policy,
                )
        except (OutboundRequestError, httpx.HTTPError) as exc:
            return ArticleResult(
                status=ResultStatus.ERROR,
                requested_url=url,
                warnings=[str(exc) or type(exc).__name__],
            )

        soup = BeautifulSoup(downloaded.content, "html.parser")
        if soup.find("input", attrs={"name": "pdfURL"}) is not None:
            return ArticleResult(
                status=ResultStatus.PARTIAL,
                requested_url=url,
                final_url=downloaded.final_url,
                title="ASX company announcement",
                warnings=["ASX 公告正文需要先确认其站点使用条款，本工具未自动代替用户确认。"],
                source=SourceReference(
                    title="ASX company announcement",
                    url=downloaded.final_url,
                    provider="ASX",
                    retrieved_at=datetime.now(UTC),
                ),
            )
        for element in soup(["script", "style", "noscript", "svg", "form", "nav", "footer"]):
            element.decompose()
        container = soup.find("article") or soup.find("main") or soup.body or soup
        paragraphs = [
            re.sub(r"\s+", " ", paragraph.get_text(" ", strip=True))
            for paragraph in container.find_all(["p", "h2", "h3", "li"])
        ]
        paragraphs = [paragraph for paragraph in paragraphs if len(paragraph) >= 30]
        text = "\n\n".join(paragraphs)[:max_chars]
        title_node = soup.find("meta", attrs={"property": "og:title"})
        title = (
            str(title_node.get("content", "")).strip()
            if title_node
            else (soup.title.get_text(" ", strip=True) if soup.title else None)
        )
        warnings = [] if text else ["article body could not be extracted from the HTML"]
        status = ResultStatus.OK if text else ResultStatus.PARTIAL
        return ArticleResult(
            status=status,
            requested_url=url,
            final_url=downloaded.final_url,
            title=title,
            text=text,
            source=SourceReference(
                title=title or downloaded.final_url,
                url=downloaded.final_url,
                provider=urlsplit(downloaded.final_url).hostname or "web",
                retrieved_at=datetime.now(UTC),
            ),
            warnings=warnings,
        )
