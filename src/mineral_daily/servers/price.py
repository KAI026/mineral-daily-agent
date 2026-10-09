from __future__ import annotations

from mcp.server import MCPServer

from mineral_daily.config import configure_logging
from mineral_daily.models import PriceResult, TrendResult
from mineral_daily.services.price import PriceService

mcp = MCPServer(
    "lme-price-mcp",
    title="Metals Price MCP",
    description="Return dated metal-market observations and trends with explicit benchmark lineage.",
    instructions=(
        "Always show provider, instrument, unit and is_proxy. Never describe a proxy as an official "
        "LME settlement price."
    ),
    version="0.1.0",
)
service = PriceService()


@mcp.tool()
async def get_price(commodity: str, date: str) -> PriceResult:
    """Return the close on, or immediately before, an ISO date (YYYY-MM-DD)."""
    return await service.get_price(commodity=commodity, requested_date=date)


@mcp.tool()
async def get_trend(commodity: str, days: int = 30, end_date: str | None = None) -> TrendResult:
    """Return recent daily observations and the first-to-last percentage change."""
    return await service.get_trend(commodity=commodity, days=days, end_date=end_date)


def main() -> None:
    configure_logging()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
