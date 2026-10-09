from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from mineral_daily.agent import orchestrator as module
from mineral_daily.agent.orchestrator import MineralDailyOrchestrator, _plan_for_prompt
from mineral_daily.models import (
    ArticleResult,
    NewsItem,
    NewsSearchResult,
    PricePoint,
    PriceResult,
    ResourceExtractionResult,
    ResourceRecord,
    ResultStatus,
    SourceReference,
    TrendResult,
)


def _source(title: str, url: str) -> SourceReference:
    return SourceReference(
        title=title,
        url=url,
        provider="test",
        retrieved_at=datetime.now(UTC),
    )


def test_project_planning_supports_fenelon_and_safe_unknowns() -> None:
    fenelon = _plan_for_prompt("生成 Fenelon 金矿简报")
    assert fenelon.topic == "Fenelon 金矿"
    assert fenelon.commodity == "gold"
    assert fenelon.resource_pages == [229]
    assert fenelon.expected_standard == "NI 43-101"

    unknown_gold = _plan_for_prompt("生成某某金矿简报")
    assert unknown_gold.commodity == "gold"
    assert unknown_gold.resource_pdf_url is None

    unknown = _plan_for_prompt("生成 Example Mine 简报")
    assert unknown.commodity == "unknown"

    oyu = _plan_for_prompt("生成 Oyu Tolgoi 铜金矿的今日简报")
    assert oyu.topic == "Oyu Tolgoi 铜金矿"
    assert oyu.commodity == "copper"
    assert oyu.commodities == ("copper", "gold")
    assert oyu.resource_pages == [17]
    assert oyu.expected_standard == "S-K 1300"

    sanshandao = _plan_for_prompt("生成三山岛金矿的今日简报")
    assert sanshandao.topic == "三山岛金矿"
    assert sanshandao.commodity == "gold"
    assert sanshandao.resource_pages == [7]

    unknown_copper_gold = _plan_for_prompt("生成 Example 铜金矿的简报")
    assert unknown_copper_gold.topic == "Example 铜金矿"
    assert unknown_copper_gold.commodity == "copper"
    assert unknown_copper_gold.commodities == ("copper", "gold")

    kainantu = _plan_for_prompt("生成 Kainantu 金铜矿的今日简报")
    assert kainantu.topic == "Kainantu 金铜矿"
    assert kainantu.commodity == "gold"
    assert kainantu.commodities == ("gold", "copper", "silver")
    assert kainantu.location == "巴布亚新几内亚东高地省"
    assert kainantu.operator == "K92 Mining Inc."
    assert kainantu.profile_url == "https://www.k92mining.com/kainantu-mine/"
    assert kainantu.resource_pages == [7]
    assert kainantu.expected_standard == "NI 43-101"

    unfamiliar_nickel = _plan_for_prompt("生成 Kambalda 镍矿的今日简报")
    assert unfamiliar_nickel.topic == "Kambalda 镍矿"
    assert unfamiliar_nickel.commodity == "nickel"
    assert unfamiliar_nickel.news_query == "Kambalda nickel mine"
    assert unfamiliar_nickel.resource_pdf_url is None

    centinela = _plan_for_prompt("生成 Centinela 铜矿的今日简报")
    assert centinela.commodity == "copper"
    assert centinela.commodities == ("copper",)
    assert centinela.news_query == "Centinela copper mine"

    centinela_without_commodity = _plan_for_prompt("生成 Centinela 项目简报")
    assert centinela_without_commodity.commodity == "unknown"
    assert centinela_without_commodity.news_query == "Centinela 项目"

    multi_metal = _plan_for_prompt("生成 Marathon 钯铜矿的今日简报")
    assert multi_metal.commodity == "palladium"
    assert multi_metal.commodities == ("palladium", "copper")
    assert multi_metal.news_query == "Marathon palladium copper mine"


def test_custom_report_does_not_reuse_registered_page_hint() -> None:
    plan = _plan_for_prompt(
        "生成 Pilbara 锂矿简报",
        "https://example.com/custom.pdf",
        [8],
        "gold",
    )
    assert plan.resource_pdf_url == "https://example.com/custom.pdf"
    assert plan.resource_pages == [8]
    assert plan.commodity == "gold"
    assert plan.expected_standard is None


class FakePool:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    async def __aenter__(self) -> FakePool:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if tool == "search":
            return NewsSearchResult(
                status=ResultStatus.OK,
                query=str(arguments["query"]),
                days=int(arguments["days"]),
                items=[
                    NewsItem(
                        title="Pilbara update",
                        url="https://example.com/news",
                        source="Example",
                        published_at=datetime.now(UTC),
                        summary="A verified update.",
                    )
                ],
                sources=[_source("feed", "https://example.com/feed")],
            ).model_dump(mode="json")
        if tool == "fetch_article":
            return ArticleResult(
                status=ResultStatus.OK,
                requested_url=str(arguments["url"]),
                final_url=str(arguments["url"]),
                title="Pilbara update",
                text="Detailed verified article text for the generated briefing.",
                source=_source("article", str(arguments["url"])),
            ).model_dump(mode="json")
        if tool == "extract_resources":
            return ResourceExtractionResult(
                status=ResultStatus.OK,
                source_url=str(arguments["pdf_url"]),
                report_standard="JORC",
                records=[
                    ResourceRecord(
                        category="indicated",
                        ore_quantity=356,
                        ore_unit="Mt",
                        grade=1.29,
                        grade_unit="% Li2O",
                        contained_metal=4.6,
                        contained_unit="Mt Li2O",
                        commodity="lithium",
                        page=2,
                        confidence=0.9,
                        evidence="Indicated 356 1.29 ... 4.6",
                    ),
                    ResourceRecord(
                        category="inferred",
                        ore_quantity=70,
                        ore_unit="Mt",
                        grade=1.25,
                        grade_unit="% Li2O",
                        contained_metal=0.9,
                        contained_unit="Mt Li2O",
                        commodity="lithium",
                        page=2,
                        confidence=0.9,
                        evidence="Inferred 70 1.25 ... 0.9",
                    ),
                ],
                pages_scanned=83,
                source=_source("report", str(arguments["pdf_url"])),
            ).model_dump(mode="json")
        if tool == "get_trend":
            return TrendResult(
                status=ResultStatus.OK,
                commodity="lithium",
                days=30,
                points=[
                    PricePoint(date=date(2026, 9, 1), close=40),
                    PricePoint(date=date(2026, 10, 1), close=44),
                ],
                change_absolute=4,
                change_percent=10,
                direction="up",
                symbol="LIT",
                instrument="Lithium ETF",
                currency="USD",
                unit="USD/share",
                provider="test",
                is_proxy=True,
                source=_source("price", "https://example.com/price"),
                warnings=["proxy benchmark"],
            ).model_dump(mode="json")
        if tool == "get_price":
            requested_date = date.fromisoformat(str(arguments["date"]))
            return PriceResult(
                status=ResultStatus.OK,
                commodity="lithium",
                requested_date=requested_date,
                observation=PricePoint(date=requested_date, close=44),
                symbol="LIT",
                instrument="Lithium ETF",
                currency="USD",
                unit="USD/share",
                provider="test",
                is_proxy=True,
                source=_source("price", "https://example.com/price"),
                warnings=["proxy benchmark"],
            ).model_dump(mode="json")
        raise AssertionError(f"unexpected tool: {server}.{tool}")


class EmptyThenExpandedNewsPool:
    def __init__(self) -> None:
        self.days_called: list[int] = []

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        assert server == "mining-news-mcp"
        assert tool == "search"
        days = int(arguments["days"])
        self.days_called.append(days)
        items = []
        if days == 180:
            items = [
                NewsItem(
                    title="Oyu Tolgoi update",
                    url="https://example.com/oyu",
                    source="Example",
                    published_at=datetime(2026, 9, 1, tzinfo=UTC),
                    summary="Verified update.",
                )
            ]
        return NewsSearchResult(
            status=ResultStatus.OK if items else ResultStatus.PARTIAL,
            query=str(arguments["query"]),
            days=days,
            items=items,
            warnings=[] if items else ["no verifiable, in-window articles matched the query"],
        ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_news_expands_window_once_when_default_window_is_empty() -> None:
    pool = EmptyThenExpandedNewsPool()

    result = await MineralDailyOrchestrator._news(
        pool,  # type: ignore[arg-type]
        "Oyu Tolgoi copper mine Rio Tinto",
        30,
        date(2026, 10, 9),
    )

    assert pool.days_called == [30, 180]
    assert result.days == 180
    assert len(result.items) == 1
    assert result.status == ResultStatus.PARTIAL
    assert any("expanded from 30 to 180" in warning for warning in result.warnings)


@pytest.mark.asyncio
async def test_agent_generates_all_required_sections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "MCPPool", FakePool)

    result = await MineralDailyOrchestrator(Path("unused.json")).generate(
        "给我生成一份关于 Pilbara 锂矿的今日简报",
        as_of=date(2026, 10, 1),
        chart_reference="assets/pilbara-price-trend.svg",
    )

    assert result.degraded is False
    assert "## 新闻摘要" in result.markdown
    assert "## 储量数据" in result.markdown
    assert "并非 Proven / Probable 矿石储量" in result.markdown
    assert "## 价格走势" in result.markdown
    assert "## 风险提示" in result.markdown
    assert "### 自动风险分析" in result.markdown
    assert "市场价格信号" in result.markdown
    assert "地质置信度提示" in result.markdown
    assert "数据截止日：2026-10-01" in result.markdown
    assert "assets/pilbara-price-trend.svg" in result.markdown
    assert "区间最高" in result.markdown
    assert result.price_chart_svg is not None
    assert [call.server for call in result.mcp_calls] == [
        "mining-news-mcp",
        "mineral-pdf-mcp",
        "lme-price-mcp",
    ]
    assert all(call.status == ResultStatus.OK for call in result.mcp_calls)
    assert "https://example.com/news" in result.markdown
    assert "Indicated" in result.markdown
    assert [event.phase for event in result.trace] == [
        "plan",
        "act",
        "observe",
        "observe",
        "render",
    ]
