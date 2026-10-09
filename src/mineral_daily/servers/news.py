from __future__ import annotations

from mcp.server import MCPServer

from mineral_daily.config import configure_logging
from mineral_daily.models import ArticleResult, NewsSearchResult
from mineral_daily.services.news import NewsService

mcp = MCPServer(
    "mining-news-mcp",
    title="Mining News MCP",
    description="Search recent mining news and extract readable article text.",
    instructions="Use search before fetch_article and preserve the returned source URLs.",
    version="0.1.0",
)
service = NewsService()


@mcp.tool()
async def search(
    query: str, days: int = 7, limit: int = 10, as_of: str | None = None
) -> NewsSearchResult:
    """Search verifiably dated mining news within the requested lookback window."""
    return await service.search(query=query, days=days, limit=limit, as_of=as_of)


@mcp.tool()
async def fetch_article(url: str, max_chars: int = 12_000) -> ArticleResult:
    """Fetch an HTTPS article and extract readable body text with SSRF and size controls."""
    return await service.fetch_article(url=url, max_chars=max_chars)


def main() -> None:
    configure_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
