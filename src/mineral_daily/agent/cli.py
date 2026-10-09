from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

from mineral_daily.agent.orchestrator import MineralDailyOrchestrator
from mineral_daily.config import configure_logging, default_local_mcp_config


def _iso_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _page_numbers(value: str) -> list[int]:
    try:
        pages = list(dict.fromkeys(int(part.strip()) for part in value.split(",")))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("pages must be comma-separated integers") from exc
    if not pages or len(pages) > 20 or any(page < 1 or page > 600 for page in pages):
        raise argparse.ArgumentTypeError("provide 1-20 page numbers between 1 and 600")
    return pages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate a sourced mineral-rights daily briefing")
    parser.add_argument(
        "--prompt",
        default="给我生成一份关于 Pilbara 锂矿的今日简报",
        help="natural-language briefing request",
    )
    parser.add_argument("--config", type=Path, help="MCP client configuration JSON")
    parser.add_argument("--resource-pdf", help="override the registry's technical-report PDF URL")
    parser.add_argument(
        "--resource-pages",
        type=_page_numbers,
        help="comma-separated 1-based PDF pages containing the resource table",
    )
    parser.add_argument(
        "--commodity",
        choices=[
            "lithium",
            "gold",
            "copper",
            "silver",
            "aluminium",
            "nickel",
            "uranium",
            "cobalt",
            "rare earth",
            "iron ore",
            "platinum",
            "palladium",
            "zinc",
            "lead",
            "tin",
            "manganese",
            "graphite",
            "phosphate",
            "potash",
        ],
        help="override commodity inference for price data",
    )
    parser.add_argument("--news-days", type=int, default=30)
    parser.add_argument("--trend-days", type=int, default=30)
    parser.add_argument(
        "--as-of",
        type=_iso_date,
        help="reproduce the news and price windows as of YYYY-MM-DD",
    )
    parser.add_argument("--output", type=Path, help="write Markdown to this path")
    parser.add_argument("--trace", action="store_true", help="print the Plan-Act-Observe trace")
    return parser


async def _run(args: argparse.Namespace) -> int:
    config = args.config or default_local_mcp_config()
    chart_path: Path | None = None
    chart_reference: str | None = None
    if args.output:
        chart_path = args.output.parent / "assets" / f"{args.output.stem}-price-trend.svg"
        chart_reference = chart_path.relative_to(args.output.parent).as_posix()
    result = await MineralDailyOrchestrator(config).generate(
        args.prompt,
        resource_pdf_url=args.resource_pdf,
        resource_pages=args.resource_pages,
        commodity=args.commodity,
        news_days=args.news_days,
        trend_days=args.trend_days,
        as_of=args.as_of,
        chart_reference=chart_reference,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(result.markdown, encoding="utf-8")
        if result.price_chart_svg and chart_path:
            chart_path.parent.mkdir(parents=True, exist_ok=True)
            chart_path.write_text(result.price_chart_svg, encoding="utf-8")
        print(f"wrote {args.output}", file=sys.stderr)
    else:
        print(result.markdown, end="")
    if args.trace:
        print("\nExecution trace:", file=sys.stderr)
        for event in result.trace:
            print(f"- {event.phase}: {event.message}", file=sys.stderr)
    return 0


def main() -> None:
    configure_logging()
    args = build_parser().parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
