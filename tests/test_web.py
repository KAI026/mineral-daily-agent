from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from mineral_daily import web
from mineral_daily.models import (
    AgentTraceEvent,
    BriefingResult,
    MCPCallSummary,
    ResultStatus,
)


class FakeOrchestrator:
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
        assert prompt == "给我生成一份关于 Pilbara 锂矿的今日简报"
        assert resource_pdf_url is None
        assert resource_pages is None
        assert commodity is None
        assert news_days == 30
        assert trend_days == 30
        assert as_of == date(2026, 10, 8)
        assert chart_reference is None
        return BriefingResult(
            markdown="# 测试简报\n\n<script>alert('x')</script>\n\n## 新闻摘要\n\n- 已核验",
            topic="Pilgangoora 锂矿",
            generated_at=datetime(2026, 10, 8, tzinfo=UTC),
            degraded=False,
            price_chart_svg='<svg xmlns="http://www.w3.org/2000/svg"><title>价格</title></svg>',
            trace=[
                AgentTraceEvent(phase="plan", message="识别项目"),
                AgentTraceEvent(phase="act", message="调用 MCP"),
                AgentTraceEvent(phase="observe", message="校验结果"),
                AgentTraceEvent(phase="render", message="生成 Markdown"),
            ],
            mcp_calls=[
                MCPCallSummary(
                    server="mining-news-mcp",
                    label="新闻聚合",
                    status=ResultStatus.OK,
                    detail="1 条窗口内新闻",
                ),
                MCPCallSummary(
                    server="mineral-pdf-mcp",
                    label="技术报告解析",
                    status=ResultStatus.OK,
                    detail="2 条资源量记录",
                ),
                MCPCallSummary(
                    server="lme-price-mcp",
                    label="价格行情",
                    status=ResultStatus.OK,
                    detail="30 个收盘观察值",
                ),
            ],
        )


def test_web_homepage_and_health() -> None:
    with TestClient(web.app) as client:
        homepage = client.get("/")
        health = client.get("/health")
        stylesheet = client.get("/assets/app.css")

    assert homepage.status_code == 200
    assert "矿权日报 Agent" in homepage.text
    assert 'id="prompt"' in homepage.text
    assert 'id="trace-list"' in homepage.text
    assert 'id="as-of"' in homepage.text
    assert 'id="commodity"' not in homepage.text
    assert 'id="resource-pdf"' not in homepage.text
    assert homepage.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in homepage.headers["content-security-policy"]
    assert health.json()["status"] == "ok"
    assert stylesheet.headers["content-type"].startswith("text/css")


def test_web_generates_sanitized_preview(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(web, "MineralDailyOrchestrator", FakeOrchestrator)
    monkeypatch.setenv("MINERAL_AGENT_MCP_CONFIG", str(tmp_path / "mcp.json"))
    payload = {
        "prompt": "给我生成一份关于 Pilbara 锂矿的今日简报",
        "news_days": 30,
        "trend_days": 30,
        "as_of": "2026-10-08",
    }

    with TestClient(web.app) as client:
        response = client.post("/api/briefings", json=payload)

    assert response.status_code == 200
    result = response.json()
    assert result["topic"] == "Pilgangoora 锂矿"
    assert len(result["mcp_calls"]) == 3
    assert [event["phase"] for event in result["trace"]] == [
        "plan",
        "act",
        "observe",
        "render",
    ]
    assert "<script>" not in result["html"]
    assert "&lt;script&gt;" in result["html"]
    assert "<svg" in result["chart_svg"]


def test_web_passes_custom_mine_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class CaptureOrchestrator:
        def __init__(self, config_path: Path) -> None:
            captured["config_path"] = config_path

        async def generate(self, prompt: str, **kwargs: object) -> BriefingResult:
            captured["prompt"] = prompt
            captured.update(kwargs)
            return BriefingResult(
                markdown="# 自定义矿权简报\n",
                topic="Example 金矿",
                generated_at=datetime(2026, 10, 8, tzinfo=UTC),
                degraded=True,
            )

    monkeypatch.setattr(web, "MineralDailyOrchestrator", CaptureOrchestrator)
    payload = {
        "prompt": "给我生成一份关于 Example 金矿的今日简报",
        "commodity": "gold",
        "resource_pdf": "https://example.com/report.pdf",
        "resource_pages": [120, 121],
    }

    with TestClient(web.app) as client:
        response = client.post("/api/briefings", json=payload)

    assert response.status_code == 200
    assert captured["commodity"] == "gold"
    assert captured["resource_pdf_url"] == "https://example.com/report.pdf"
    assert captured["resource_pages"] == [120, 121]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"prompt": "x"},
        {"prompt": "合法提示词", "news_days": 0},
        {"prompt": "合法提示词", "trend_days": 500},
        {"prompt": "合法提示词", "as_of": "not-a-date"},
        {"prompt": "合法提示词", "resource_pages": [0]},
        {"prompt": "合法提示词", "commodity": "unobtainium"},
    ],
)
def test_web_rejects_invalid_input(payload: dict[str, object]) -> None:
    with TestClient(web.app) as client:
        response = client.post("/api/briefings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]


def test_web_rejects_oversized_request() -> None:
    with TestClient(web.app) as client:
        response = client.post(
            "/api/briefings",
            content=b"x" * (web.MAX_REQUEST_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 413
