from __future__ import annotations

import os
from datetime import UTC, date, datetime
from importlib.resources import files
from pathlib import Path
from typing import Literal

import uvicorn
from markdown_it import MarkdownIt
from pydantic import BaseModel, Field, ValidationError, field_validator
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.routing import Route

from mineral_daily.agent.orchestrator import MineralDailyOrchestrator
from mineral_daily.config import default_local_mcp_config

MAX_REQUEST_BYTES = 16 * 1024
ASSET_PACKAGE = "mineral_daily.web_assets"
MARKDOWN = MarkdownIt(
    "commonmark",
    {"html": False, "linkify": False, "typographer": False},
).enable("table")


class WebBriefingRequest(BaseModel):
    prompt: str = Field(min_length=4, max_length=500)
    news_days: int = Field(default=30, ge=1, le=365)
    trend_days: int = Field(default=30, ge=2, le=365)
    as_of: date | None = None
    resource_pdf: str | None = Field(default=None, max_length=2_048)
    resource_pages: list[int] | None = Field(default=None, max_length=20)
    commodity: Literal[
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
    ] | None = None

    @field_validator("prompt")
    @classmethod
    def normalize_prompt(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("提示词不能为空")
        return normalized

    @field_validator("resource_pdf")
    @classmethod
    def normalize_resource_pdf(cls, value: str | None) -> str | None:
        normalized = value.strip() if value else None
        return normalized or None

    @field_validator("resource_pages")
    @classmethod
    def validate_resource_pages(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if any(page < 1 or page > 600 for page in value):
            raise ValueError("资源表页码必须在 1 到 600 之间")
        return list(dict.fromkeys(value)) or None


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'; object-src 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response


def _asset_text(name: str) -> str:
    return files(ASSET_PACKAGE).joinpath(name).read_text(encoding="utf-8")


def _config_path() -> Path:
    configured = os.getenv("MINERAL_AGENT_MCP_CONFIG")
    return Path(configured) if configured else default_local_mcp_config()


async def homepage(_: Request) -> HTMLResponse:
    return HTMLResponse(_asset_text("index.html"))


async def stylesheet(_: Request) -> Response:
    return Response(_asset_text("app.css"), media_type="text/css; charset=utf-8")


async def javascript(_: Request) -> Response:
    return Response(_asset_text("app.js"), media_type="application/javascript; charset=utf-8")


async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "mineral-daily-web",
            "time": datetime.now(UTC).isoformat(),
        }
    )


async def generate_briefing(request: Request) -> JSONResponse:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_REQUEST_BYTES:
        return JSONResponse({"error": "请求内容过大"}, status_code=413)
    body = await request.body()
    if len(body) > MAX_REQUEST_BYTES:
        return JSONResponse({"error": "请求内容过大"}, status_code=413)
    try:
        raw = await request.json()
        payload = WebBriefingRequest.model_validate(raw)
    except (ValueError, ValidationError):
        return JSONResponse(
            {"error": "输入无效，请检查提示词、日期和天数范围"},
            status_code=422,
        )

    try:
        result = await MineralDailyOrchestrator(_config_path()).generate(
            payload.prompt,
            resource_pdf_url=payload.resource_pdf,
            resource_pages=payload.resource_pages,
            commodity=payload.commodity,
            news_days=payload.news_days,
            trend_days=payload.trend_days,
            as_of=payload.as_of,
        )
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except Exception:
        return JSONResponse(
            {"error": "Agent 运行失败，请检查 Docker 日志后重试"},
            status_code=500,
        )

    return JSONResponse(
        {
            "topic": result.topic,
            "generated_at": result.generated_at.isoformat(),
            "degraded": result.degraded,
            "markdown": result.markdown,
            "html": MARKDOWN.render(result.markdown),
            "chart_svg": result.price_chart_svg,
            "trace": [event.model_dump(mode="json") for event in result.trace],
            "mcp_calls": [call.model_dump(mode="json") for call in result.mcp_calls],
        }
    )


routes = [
    Route("/", homepage),
    Route("/assets/app.css", stylesheet),
    Route("/assets/app.js", javascript),
    Route("/health", health),
    Route("/api/briefings", generate_briefing, methods=["POST"]),
]

app = Starlette(debug=False, routes=routes)
app.add_middleware(SecurityHeadersMiddleware)


def main() -> None:
    host = os.getenv("MINERAL_AGENT_WEB_HOST", "0.0.0.0")
    port = int(os.getenv("MINERAL_AGENT_WEB_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, server_header=False)


if __name__ == "__main__":
    main()
