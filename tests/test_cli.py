from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from mineral_daily.agent import cli
from mineral_daily.models import BriefingResult


@pytest.mark.asyncio
async def test_cli_writes_markdown_and_chart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    class FakeOrchestrator:
        def __init__(self, config: Path) -> None:
            captured["config"] = config

        async def generate(self, prompt: str, **kwargs: object) -> BriefingResult:
            captured["prompt"] = prompt
            captured.update(kwargs)
            return BriefingResult(
                markdown="# 简报\n",
                topic="test",
                generated_at=datetime.now(UTC),
                degraded=False,
                price_chart_svg="<svg></svg>\n",
            )

    monkeypatch.setattr(cli, "MineralDailyOrchestrator", FakeOrchestrator)
    output = tmp_path / "briefing.md"
    args = cli.build_parser().parse_args(
        [
            "--config",
            str(tmp_path / "mcp.json"),
            "--output",
            str(output),
            "--as-of",
            "2026-10-01",
            "--commodity",
            "gold",
            "--resource-pages",
            "2,229",
        ]
    )

    assert await cli._run(args) == 0
    assert output.read_text(encoding="utf-8") == "# 简报\n"
    assert (tmp_path / "assets" / "briefing-price-trend.svg").exists()
    assert str(captured["as_of"]) == "2026-10-01"
    assert captured["chart_reference"] == "assets/briefing-price-trend.svg"
    assert captured["commodity"] == "gold"
    assert captured["resource_pages"] == [2, 229]


def test_cli_rejects_invalid_as_of() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--as-of", "01/10/2026"])


def test_cli_rejects_invalid_resource_pages() -> None:
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["--resource-pages", "0,wrong"])
