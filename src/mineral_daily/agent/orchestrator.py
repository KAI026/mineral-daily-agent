from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from mineral_daily.agent.mcp_client import MCPPool, MCPToolError
from mineral_daily.chart import build_price_trend_svg
from mineral_daily.config import load_project_registry
from mineral_daily.models import (
    AgentTraceEvent,
    ArticleResult,
    BriefingResult,
    MCPCallSummary,
    NewsSearchResult,
    PriceResult,
    ResourceExtractionResult,
    ResultStatus,
    TrendResult,
)


@dataclass(frozen=True, slots=True)
class ProjectPlan:
    topic: str
    news_query: str
    commodity: str
    commodities: tuple[str, ...]
    location: str | None
    operator: str | None
    profile_url: str | None
    resource_pdf_url: str | None
    resource_pages: list[int] | None
    expected_standard: str | None


COMMODITY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("lithium", ("锂", "lithium")),
    ("gold", ("金矿", "黄金", "gold")),
    ("copper", ("铜", "copper")),
    ("silver", ("银矿", "白银", "silver")),
    ("aluminium", ("铝", "aluminium", "aluminum")),
    ("nickel", ("镍", "nickel")),
    ("uranium", ("铀", "uranium")),
    ("cobalt", ("钴", "cobalt")),
    ("rare earth", ("稀土", "rare earth")),
    ("iron ore", ("铁矿石", "iron ore")),
    ("platinum", ("铂金", "铂矿", "platinum")),
    ("palladium", ("钯", "palladium")),
    ("zinc", ("锌", "zinc")),
    ("lead", ("铅", "lead")),
    ("tin", ("锡", "tin")),
    ("manganese", ("锰", "manganese")),
    ("graphite", ("石墨", "graphite")),
    ("phosphate", ("磷矿", "phosphate")),
    ("potash", ("钾盐", "potash")),
)

COMMODITY_LABELS = {
    "gold": "黄金",
    "copper": "铜",
    "silver": "白银",
    "lithium": "锂",
    "aluminium": "铝",
    "nickel": "镍",
    "uranium": "铀",
    "cobalt": "钴",
    "rare earth": "稀土",
    "iron ore": "铁矿石",
    "platinum": "铂金",
    "palladium": "钯",
    "zinc": "锌",
    "lead": "铅",
    "tin": "锡",
    "manganese": "锰",
    "graphite": "石墨",
    "phosphate": "磷矿",
    "potash": "钾盐",
}

NEWS_RISK_PATTERNS: tuple[tuple[str, str, str], ...] = (
    (
        "资产与所有权",
        r"\b(?:sell|sale|offload|divest|acqui(?:re|sition)|takeover|asset deal)\w*\b|"
        r"出售|收购|并购|剥离|资产交易",
        "标题或摘要出现出售、收购、剥离或资产交易相关词，项目所有权或加工安排可能变化",
    ),
    (
        "运营",
        r"\b(?:shutdown|suspend|delay|accident|fatal|strike|production cut|cost overrun)\b|"
        r"停产|暂停|延误|事故|伤亡|罢工|减产|超支",
        "标题或摘要出现停产、延误、事故、罢工、减产或成本超支相关词",
    ),
    (
        "政策与许可",
        r"\b(?:permit|licen[cs]e|royalt|regulat|export ban|tax increase|government review)\w*\b|"
        r"许可|监管|特许权|出口禁令|加税|政府审查",
        "标题或摘要出现许可、监管、税费或出口限制相关词",
    ),
    (
        "环境与社区",
        r"\b(?:protest|community dispute|environment|tailings|water shortage|pollution)\w*\b|"
        r"抗议|社区纠纷|环保|尾矿|缺水|污染",
        "标题或摘要出现环境、尾矿、水资源或社区关系相关词",
    ),
    (
        "融资",
        r"\b(?:debt|financ|funding|dilution|cash shortfall|loss)\w*\b|"
        r"债务|融资|摊薄|资金短缺|亏损",
        "标题或摘要出现债务、融资、股权摊薄或资金压力相关词",
    ),
    (
        "地缘政治",
        r"\b(?:sanction|conflict|war|political unrest|border closure)\w*\b|"
        r"制裁|冲突|战争|政治动荡|边境关闭",
        "标题或摘要出现制裁、冲突或政治动荡相关词",
    ),
)


def _infer_commodities(prompt: str) -> tuple[str, ...]:
    folded = prompt.casefold()
    matches: list[tuple[int, int, str]] = []
    for commodity, aliases in COMMODITY_ALIASES:
        for alias in aliases:
            if re.fullmatch(r"[a-z ]+", alias, re.IGNORECASE):
                match = re.search(
                    rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
                    folded,
                )
                position = match.start() if match else -1
            else:
                position = folded.find(alias)
            if position >= 0:
                matches.append((position, -len(alias), commodity))
    ordered: list[str] = []
    for _, _, commodity in sorted(matches):
        if commodity not in ordered:
            ordered.append(commodity)
    return tuple(ordered) or ("unknown",)


def _infer_commodity(prompt: str) -> str:
    return _infer_commodities(prompt)[0]


def _generic_news_query(topic: str, commodities: tuple[str, ...]) -> str:
    recognized = tuple(item for item in commodities if item != "unknown")
    if not recognized:
        return topic
    project = topic
    aliases = tuple(
        alias
        for canonical, values in COMMODITY_ALIASES
        if canonical in recognized
        for alias in values
    )
    for alias in sorted(aliases, key=len, reverse=True):
        if re.fullmatch(r"[a-z ]+", alias, re.IGNORECASE):
            project = re.sub(
                rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])",
                " ",
                project,
                flags=re.IGNORECASE,
            )
        else:
            project = project.replace(alias, " ")
    project = re.sub(r"\b(?:mine|mining|project|deposit)\b|矿山|矿区|矿床|矿", " ", project, flags=re.IGNORECASE)
    project = re.sub(r"\s+", " ", project).strip(" ，。,.")
    return f"{project} {' '.join(recognized)} mine".strip()


def _plan_for_prompt(
    prompt: str,
    resource_pdf_url: str | None = None,
    resource_pages: list[int] | None = None,
    commodity: str | None = None,
) -> ProjectPlan:
    registry = load_project_registry()
    folded = prompt.casefold()
    for project in registry.values():
        aliases = [str(alias).casefold() for alias in project.get("aliases", [])]
        if any(alias in folded for alias in aliases):
            custom_report = resource_pdf_url is not None
            primary_commodity = commodity or str(project["commodity"])
            registered_commodities = tuple(
                str(item) for item in project.get("commodities", [primary_commodity])
            )
            return ProjectPlan(
                topic=str(project["display_name"]),
                news_query=str(project["news_query"]),
                commodity=primary_commodity,
                commodities=(
                    (primary_commodity,)
                    if commodity is not None
                    else registered_commodities
                ),
                location=str(project.get("location") or "") or None,
                operator=str(project.get("operator") or "") or None,
                profile_url=str(project.get("profile_url") or "") or None,
                resource_pdf_url=resource_pdf_url or str(project["resource_pdf_url"]),
                resource_pages=(
                    resource_pages
                    if resource_pages is not None
                    else (
                        None
                        if custom_report
                        else [int(page) for page in project.get("resource_pages", [])] or None
                    )
                ),
                expected_standard=(
                    None
                    if custom_report
                    else str(project.get("resource_standard") or "") or None
                ),
            )
    topic = re.sub(r"(?:请|给我|生成|一份|关于|今日|日报|简报)", " ", prompt)
    topic = re.sub(r"\s+", " ", topic).strip(" ，。,.\t") or prompt.strip()
    topic = re.sub(r"的$", "", topic).strip() or prompt.strip()
    inferred_commodities = _infer_commodities(prompt)
    primary_commodity = commodity or inferred_commodities[0]
    planned_commodities = (
        (primary_commodity,) if commodity is not None else inferred_commodities
    )
    return ProjectPlan(
        topic=topic,
        news_query=_generic_news_query(topic, planned_commodities),
        commodity=primary_commodity,
        commodities=planned_commodities,
        location=None,
        operator=None,
        profile_url=None,
        resource_pdf_url=resource_pdf_url,
        resource_pages=resource_pages,
        expected_standard=None,
    )


def _error_model[ModelT: BaseModel](model: type[ModelT], **values: Any) -> ModelT:
    payload = {"status": ResultStatus.ERROR, "warnings": [values.pop("error")]}
    payload.update(values)
    return model.model_validate(payload)


def _combined_status(*statuses: ResultStatus) -> ResultStatus:
    if all(status == ResultStatus.ERROR for status in statuses):
        return ResultStatus.ERROR
    if any(status != ResultStatus.OK for status in statuses):
        return ResultStatus.PARTIAL
    return ResultStatus.OK


class MineralDailyOrchestrator:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path

    async def generate(
        self,
        prompt: str,
        *,
        resource_pdf_url: str | None = None,
        resource_pages: list[int] | None = None,
        commodity: str | None = None,
        news_days: int = 30,
        trend_days: int = 30,
        as_of: date | None = None,
        chart_reference: str | None = None,
    ) -> BriefingResult:
        today = datetime.now(UTC).date()
        if as_of and as_of > today:
            raise ValueError("as_of cannot be in the future")
        effective_date = as_of or today
        plan = _plan_for_prompt(prompt, resource_pdf_url, resource_pages, commodity)
        trace = [
            AgentTraceEvent(
                phase="plan",
                message=(
                    f"主题={plan.topic}; 新闻窗口={news_days}天; 商品={plan.commodity}; "
                    f"新闻查询={plan.news_query}; "
                    f"资源报告={'已配置' if plan.resource_pdf_url else '未配置'}"
                ),
            )
        ]
        async with MCPPool(self.config_path) as pool:
            trace.append(AgentTraceEvent(phase="act", message="并行调用新闻、资源 PDF 与价格 MCP"))
            news_task = self._news(pool, plan.news_query, news_days, as_of)
            price_task = self._prices(pool, plan.commodity, trend_days, effective_date)
            resource_task = self._resources(pool, plan.resource_pdf_url, plan.resource_pages)
            news, (trend, price), resources = await asyncio.gather(
                news_task, price_task, resource_task
            )
            trace.append(
                AgentTraceEvent(
                    phase="observe",
                    message=(
                        f"新闻={news.status}/{len(news.items)}条; "
                        f"资源={resources.status}/{len(resources.records)}行; "
                        f"价格={trend.status}/{len(trend.points)}点"
                    ),
                )
            )
            articles: list[ArticleResult] = []
            for item in news.items[:3]:
                try:
                    payload = await pool.call(
                        "mining-news-mcp",
                        "fetch_article",
                        {"url": item.url, "max_chars": 6_000},
                    )
                    articles.append(ArticleResult.model_validate(payload))
                except (MCPToolError, Exception) as exc:
                    articles.append(
                        _error_model(
                            ArticleResult,
                            requested_url=item.url,
                            error=f"article extraction failed: {exc}",
                        )
                    )
            trace.append(
                AgentTraceEvent(
                    phase="observe",
                    message=f"全文抽取完成：{sum(article.status != ResultStatus.ERROR for article in articles)}篇可用",
                )
            )

        generated_at = datetime.now(UTC)
        price_chart_svg = build_price_trend_svg(trend)
        markdown = render_briefing(
            plan=plan,
            news=news,
            articles=articles,
            resources=resources,
            trend=trend,
            price=price,
            generated_at=generated_at,
            as_of=effective_date,
            chart_reference=chart_reference if price_chart_svg else None,
        )
        trace.append(AgentTraceEvent(phase="render", message="按来源约束生成 Markdown 简报"))
        degraded = any(
            result.status != ResultStatus.OK for result in (news, resources, trend, price)
        )
        price_status = _combined_status(trend.status, price.status)
        return BriefingResult(
            markdown=markdown,
            topic=plan.topic,
            generated_at=generated_at,
            degraded=degraded,
            price_chart_svg=price_chart_svg,
            trace=trace,
            mcp_calls=[
                MCPCallSummary(
                    server="mining-news-mcp",
                    label="新闻聚合",
                    status=news.status,
                    detail=f"{len(news.items)} 条可验证新闻（{news.days} 天窗口）",
                ),
                MCPCallSummary(
                    server="mineral-pdf-mcp",
                    label="技术报告解析",
                    status=resources.status,
                    detail=f"{len(resources.records)} 条解析记录",
                ),
                MCPCallSummary(
                    server="lme-price-mcp",
                    label="价格行情",
                    status=price_status,
                    detail=f"{len(trend.points)} 个收盘观察值",
                ),
            ],
        )

    @staticmethod
    async def _news(pool: MCPPool, query: str, days: int, as_of: date | None) -> NewsSearchResult:
        try:
            arguments: dict[str, Any] = {"query": query, "days": days, "limit": 8}
            if as_of:
                arguments["as_of"] = as_of.isoformat()
            payload = await pool.call("mining-news-mcp", "search", arguments)
            result = NewsSearchResult.model_validate(payload)
            if result.items or days >= 180:
                return result
            arguments["days"] = 180
            expanded_payload = await pool.call("mining-news-mcp", "search", arguments)
            expanded = NewsSearchResult.model_validate(expanded_payload)
            expanded.warnings = list(
                dict.fromkeys(
                    [
                        *expanded.warnings,
                        f"news window was expanded from {days} to 180 days after no matches",
                    ]
                )
            )
            if expanded.items and expanded.status == ResultStatus.OK:
                expanded.status = ResultStatus.PARTIAL
            return expanded
        except Exception as exc:
            return _error_model(
                NewsSearchResult, query=query, days=days, error=f"news MCP failed: {exc}"
            )

    @staticmethod
    async def _resources(
        pool: MCPPool, resource_pdf_url: str | None, resource_pages: list[int] | None
    ) -> ResourceExtractionResult:
        if not resource_pdf_url:
            return _error_model(
                ResourceExtractionResult,
                source_url="",
                error="no resource PDF URL was configured for this project",
            )
        try:
            arguments: dict[str, Any] = {"pdf_url": resource_pdf_url}
            if resource_pages:
                arguments["page_numbers"] = resource_pages
            payload = await pool.call("mineral-pdf-mcp", "extract_resources", arguments)
            return ResourceExtractionResult.model_validate(payload)
        except Exception as exc:
            return _error_model(
                ResourceExtractionResult,
                source_url=resource_pdf_url,
                error=f"PDF MCP failed: {exc}",
            )

    @staticmethod
    async def _prices(
        pool: MCPPool, commodity: str, days: int, as_of: date
    ) -> tuple[TrendResult, PriceResult]:
        try:
            trend_payload = await pool.call(
                "lme-price-mcp",
                "get_trend",
                {"commodity": commodity, "days": days, "end_date": as_of.isoformat()},
            )
            trend = TrendResult.model_validate(trend_payload)
        except Exception as exc:
            trend = _error_model(
                TrendResult, commodity=commodity, days=days, error=f"price MCP failed: {exc}"
            )
        try:
            price_payload = await pool.call(
                "lme-price-mcp",
                "get_price",
                {"commodity": commodity, "date": as_of.isoformat()},
            )
            price = PriceResult.model_validate(price_payload)
        except Exception as exc:
            price = _error_model(
                PriceResult,
                commodity=commodity,
                requested_date=as_of,
                error=f"price MCP failed: {exc}",
            )
        return trend, price


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:,.{digits}f}"


def _standard_family(value: str) -> str:
    folded = value.casefold()
    if "jorc" in folded:
        return "jorc"
    if "43-101" in folded or "43 101" in folded:
        return "ni 43-101"
    return folded.strip()


def _warning_zh(warning: str) -> str:
    translations = {
        "no verifiable, in-window articles matched the query": (
            "没有找到发布日期可验证且位于检索窗口内的匹配新闻。"
        ),
        "the document does not identify itself as NI 43-101; extracted values must not be presented as NI 43-101 data": (
            "该报告未标识为 NI 43-101，提取结果不得表述为 NI 43-101 数据。"
        ),
        "this is a public market proxy, not an official LME settlement price; the source and instrument are returned explicitly to prevent benchmark confusion": (
            "该行情是公开市场代理，不是官方 LME 结算价；报告已明确标注来源和金融工具。"
        ),
        "no observation was available on or before the requested date": (
            "请求日期及此前没有可用行情观察值。"
        ),
        "fewer than two observations were available": "可用行情观察值少于两个，无法判断趋势。",
        "no standalone Indicated or Inferred table rows were found": (
            "未找到独立的 Indicated 或 Inferred 表格行。"
        ),
        "contained metal was left null where the column mapping was ambiguous": (
            "金属量列映射不明确，相应字段保持为空。"
        ),
    }
    if warning in translations:
        return translations[warning]
    match = re.fullmatch(r"feed unavailable \(([^)]+)\): (.+)", warning)
    if match:
        detail = re.sub(r"^upstream returned HTTP", "上游返回 HTTP", match.group(2))
        return f"新闻源 {match.group(1)} 暂不可用：{detail}"
    match = re.fullmatch(
        r"used a bundled text snapshot verified against the linked source on ([^;]+); "
        r"the live PDF was not downloaded",
        warning,
    )
    if match:
        return f"使用了在 {match.group(1)} 对照原始链接核验的文本快照，本次未实时下载 PDF。"
    match = re.fullmatch(r"daily return volatility \(population stdev\): (.+)", warning)
    if match:
        return f"区间日收益率波动率（总体标准差）为 {match.group(1)}。"
    match = re.fullmatch(
        r"no observation exists for the requested date; returned the latest prior close from (.+)",
        warning,
    )
    if match:
        return f"请求日期没有行情，已返回 {match.group(1)} 的最近前收盘价。"
    match = re.fullmatch(
        r"news window was expanded from (\d+) to (\d+) days after no matches", warning
    )
    if match:
        return f"近 {match.group(1)} 天无匹配新闻，已自动扩大到 {match.group(2)} 天。"
    match = re.fullmatch(
        r"instrument ([A-Z0-9.-]+) was dynamically discovered from Nasdaq autocomplete "
        r"for commodity '([^']+)'",
        warning,
    )
    if match:
        return (
            f"行情工具通过 Nasdaq 搜索为“{match.group(2)}”动态发现了 {match.group(1)}；"
            "报告已按代理工具展示，仍需复核其成分和跟踪口径。"
        )
    if re.search(r"[\u4e00-\u9fff]", warning):
        return warning
    return f"技术告警（上游原文）：{warning}"


def _derive_risk_signals(
    news: NewsSearchResult,
    resources: ResourceExtractionResult,
    trend: TrendResult,
) -> list[str]:
    signals: list[str] = []
    matched_news_risk = False
    for label, pattern, explanation in NEWS_RISK_PATTERNS:
        for item in news.items:
            haystack = f"{item.title} {item.summary}"
            if not re.search(pattern, haystack, re.IGNORECASE):
                continue
            signals.append(
                f"**{label}信号**：{explanation}；请核对原文：[{item.title}]({item.url})。"
            )
            matched_news_risk = True
            break
    if news.items and not matched_news_risk:
        signals.append(
            "**事件扫描**：已检索新闻标题和摘要中未命中预设高风险关键词；"
            "这不代表项目不存在未披露或未被抓取的风险。"
        )
    elif not news.items:
        signals.append("**事件盲区**：没有取得项目级新闻，无法据此判断近期运营或政策事件。")

    if trend.change_percent is not None:
        movement = abs(trend.change_percent)
        instrument = trend.instrument or trend.symbol or trend.commodity
        if movement >= 10:
            level = "较大"
        elif movement >= 5:
            level = "明显"
        else:
            level = "有限"
        signals.append(
            f"**市场价格信号**：{instrument} 在当前窗口变化 "
            f"{trend.change_percent:+.2f}%，方向性变动{level}；该指标是市场代理，不是矿山售价。"
        )
    else:
        signals.append("**市场价格盲区**：没有足够行情观察值，无法量化价格方向和波动。")

    categories = {record.category for record in resources.records}
    if not resources.records:
        signals.append("**地质数据风险**：未取得可信资源量或储量记录，不能评估项目资源基础。")
    elif categories == {"inferred"}:
        signals.append(
            "**地质置信度风险**：披露记录仅包含 Inferred，不能等同于较高置信度资源或矿石储量。"
        )
    elif "inferred" in categories:
        signals.append(
            "**地质置信度提示**：结果包含 Inferred；该分类的不确定性高于 Measured / Indicated，"
            "不能直接作为可采储量。"
        )
    return signals


def render_briefing(
    *,
    plan: ProjectPlan,
    news: NewsSearchResult,
    articles: list[ArticleResult],
    resources: ResourceExtractionResult,
    trend: TrendResult,
    price: PriceResult,
    generated_at: datetime,
    as_of: date,
    chart_reference: str | None = None,
) -> str:
    report_title = (
        f"{plan.topic}权日报" if plan.topic.endswith("矿") else f"{plan.topic}矿权日报"
    )
    lines = [
        f"# {report_title}",
        "",
        f"> 生成时间：{generated_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')}  ",
        f"> 数据截止日：{as_of.isoformat()}  ",
        f"> 数据状态：{'存在降级，详见风险提示' if any(r.status != ResultStatus.OK for r in (news, resources, trend, price)) else '全部工具调用成功'}",
        "",
        "## 执行摘要",
        "",
    ]
    if news.items:
        lines.append(f"近 {news.days} 天检索到 {len(news.items)} 条可验证日期的相关报道。")
    else:
        lines.append(f"近 {news.days} 天未获得可验证日期的相关新闻，简报不据此推断最新事件。")
    target_resources = resources.records
    if target_resources:
        standard = resources.report_standard or "未识别报告标准"
        lines.append(f"资源报告按 {standard} 标识，提取到 {len(target_resources)} 条目标分类记录。")
    else:
        lines.append("资源数据未达到可引用条件，相关字段保持空缺。")
    if trend.points and trend.change_percent is not None:
        lines.append(
            f"价格代理工具返回 {len(trend.points)} 个收盘观察值，区间变化 {trend.change_percent:+.2f}%。"
        )
    else:
        lines.append("价格趋势不可用，不对价格方向作判断。")
    if len(plan.commodities) > 1:
        primary_label = COMMODITY_LABELS.get(plan.commodity, plan.commodity)
        scope = "、".join(COMMODITY_LABELS.get(item, item) for item in plan.commodities)
        lines.append(
            f"该项目涉及 {scope}；价格章节仅以主商品“{primary_label}”的公开市场代理展示。"
        )
    if plan.location or plan.operator:
        profile_parts = [part for part in (plan.location, plan.operator) if part]
        profile_text = "；".join(profile_parts)
        if plan.profile_url:
            lines.append(f"项目登记信息：{profile_text}（[项目官方页面]({plan.profile_url})）。")
        else:
            lines.append(f"项目登记信息：{profile_text}。")

    lines.extend(["", "## 新闻摘要", ""])
    article_by_url = {article.requested_url: article for article in articles}
    if not news.items:
        lines.append("- 暂无满足时间窗口和日期校验条件的新闻。")
    for item in news.items[:5]:
        article = article_by_url.get(item.url)
        body = article.text if article and article.text else item.summary
        body = re.sub(r"\s+", " ", body).strip()
        comparison_body = re.sub(r"[^\w\u4e00-\u9fff]+", "", body).casefold()
        comparison_title = re.sub(r"[^\w\u4e00-\u9fff]+", "", item.title).casefold()
        if comparison_body == comparison_title or (
            comparison_body.startswith(comparison_title)
            and len(comparison_body) - len(comparison_title) <= 30
        ):
            body = "仅获得可验证标题；未从上游页面提取到可引用正文。"
        if len(body) > 240:
            body = body[:237].rstrip() + "..."
        date_text = item.published_at.date().isoformat() if item.published_at else "日期未知"
        lines.append(
            f"- [{item.title}]({item.url})（{item.source}，{date_text}）：{body or '仅获得标题。'}"
        )

    lines.extend(
        [
            "",
            "## 储量数据",
            "",
            "> 口径说明：分类按来源原样保留；Indicated / Inferred 属于矿产资源量分类，并非 Proven / Probable 矿石储量；来源标为储量时则按原分类展示。",
            "",
        ]
    )
    if target_resources:
        lines.extend(
            [
                "| 分类 | 矿种 | 矿石量 | 品位 | 金属量 | 页码 | 置信度 |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        labels = {
            "measured": "Measured",
            "indicated": "Indicated",
            "measured_and_indicated": "Measured & Indicated",
            "inferred": "Inferred",
            "resource_total": "Resource total",
            "reserve_total": "Reserve total",
        }
        for record in target_resources:
            commodity_label = COMMODITY_LABELS.get(
                record.commodity or "", record.commodity or "未注明"
            )
            ore_quantity = (
                f"{_fmt(record.ore_quantity)} {record.ore_unit}"
                if record.ore_quantity is not None and record.ore_unit
                else "未单独披露"
            )
            grade = (
                f"{_fmt(record.grade)} {record.grade_unit}"
                if record.grade is not None and record.grade_unit
                else "未单独披露"
            )
            contained = (
                f"{_fmt(record.contained_metal)} {record.contained_unit}"
                if record.contained_metal is not None and record.contained_unit
                else "未可靠映射"
            )
            lines.append(
                f"| {labels.get(record.category, record.category)} | {commodity_label} | "
                f"{ore_quantity} | {grade} | {contained} | "
                f"{record.page} | {record.confidence:.0%} |"
            )
        if resources.source_url:
            lines.append("")
            lines.append(f"来源：[资源/储量披露]({resources.source_url})。")
    else:
        lines.append("未从可信披露中获得可引用的资源量或储量记录。")

    lines.extend(["", "## 价格走势", ""])
    if price.observation:
        lines.append(
            f"最近可用收盘：{price.observation.date}，{_fmt(price.observation.close, 4)} "
            f"{price.unit or ''}（{price.instrument or price.symbol}）。"
        )
    if trend.points and trend.change_percent is not None:
        highest = max(trend.points, key=lambda point: point.close)
        lowest = min(trend.points, key=lambda point: point.close)
        lines.append(
            f"窗口首尾：{trend.points[0].date} 的 {_fmt(trend.points[0].close, 4)} → "
            f"{trend.points[-1].date} 的 {_fmt(trend.points[-1].close, 4)}，"
            f"变化 {trend.change_percent:+.2f}%。"
        )
        lines.extend(
            [
                "",
                "| 指标 | 数值 |",
                "|---|---:|",
                f"| 观察值 | {len(trend.points)} 个交易日 |",
                f"| 区间最高 | {_fmt(highest.close, 4)} {trend.unit or ''}（{highest.date}） |",
                f"| 区间最低 | {_fmt(lowest.close, 4)} {trend.unit or ''}（{lowest.date}） |",
                f"| 区间变化 | {trend.change_percent:+.2f}% |",
            ]
        )
    if chart_reference:
        lines.extend(
            [
                "",
                f"![{trend.instrument or trend.symbol or trend.commodity} 价格走势]({chart_reference})",
                "",
                "> 图表口径：公开市场代理走势，不代表官方交易所结算价、矿产品现货价或矿山实际售价。",
            ]
        )
    if trend.source:
        lines.append(f"来源：[行情接口与具体合约]({trend.source.url})。")

    warnings = list(
        dict.fromkeys(news.warnings + resources.warnings + trend.warnings + price.warnings)
    )
    if len(plan.commodities) > 1:
        warnings.append(
            "多金属项目的价格图仅展示主商品代理，不能代表项目内其他金属或矿山实际销售价格。"
        )
    if plan.expected_standard and resources.report_standard:
        if _standard_family(plan.expected_standard) != _standard_family(resources.report_standard):
            warnings.append(
                f"项目登记标准为 {plan.expected_standard}，解析器识别为 {resources.report_standard}。"
            )
    risk_signals = _derive_risk_signals(news, resources, trend)
    lines.extend(["", "## 风险提示", "", "### 自动风险分析", ""])
    for signal in risk_signals:
        lines.append(f"- {signal}")
    lines.extend(["", "### 数据源与口径告警", ""])
    if not warnings:
        lines.append("- 未发现工具级告警；仍应在投资或技术决策前复核原始披露。")
    else:
        for warning in warnings:
            lines.append(f"- {_warning_zh(warning)}")
    lines.append("- 本简报是信息聚合结果，不构成投资、法律或矿业技术意见。")

    sources: dict[str, str] = {}
    for item in news.items:
        sources[item.url] = item.title
    if resources.source_url:
        sources[resources.source_url] = "资源/储量披露"
    if trend.source:
        sources[trend.source.url] = trend.source.title
    if plan.profile_url:
        sources[plan.profile_url] = "项目官方页面"
    lines.extend(["", "## 来源索引", ""])
    if sources:
        for index, (url, title) in enumerate(sources.items(), start=1):
            lines.append(f"{index}. [{title}]({url})")
    else:
        lines.append("本次运行没有获得可引用来源。")
    return "\n".join(lines).rstrip() + "\n"
