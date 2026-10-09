from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


class ResultStatus(StrEnum):
    OK = "ok"
    PARTIAL = "partial"
    ERROR = "error"


class SourceReference(BaseModel):
    title: str
    url: str
    provider: str
    retrieved_at: datetime


class NewsItem(BaseModel):
    title: str
    url: str
    source: str
    published_at: datetime | None = None
    summary: str = ""


class NewsSearchResult(BaseModel):
    status: ResultStatus
    query: str
    days: int
    items: list[NewsItem] = Field(default_factory=list)
    sources: list[SourceReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ArticleResult(BaseModel):
    status: ResultStatus
    requested_url: str
    final_url: str | None = None
    title: str | None = None
    text: str = ""
    published_at: datetime | None = None
    source: SourceReference | None = None
    warnings: list[str] = Field(default_factory=list)


class ResourceRecord(BaseModel):
    category: Literal[
        "measured",
        "indicated",
        "measured_and_indicated",
        "inferred",
        "resource_total",
        "reserve_total",
    ]
    ore_quantity: float | None = None
    ore_unit: str | None = None
    grade: float | None = None
    grade_unit: str | None = None
    contained_metal: float | None = None
    contained_unit: str | None = None
    commodity: str | None = None
    page: int
    confidence: float = Field(ge=0, le=1)
    evidence: str


class ResourceExtractionResult(BaseModel):
    status: ResultStatus
    source_url: str
    report_standard: str | None = None
    records: list[ResourceRecord] = Field(default_factory=list)
    pages_scanned: int = 0
    source: SourceReference | None = None
    warnings: list[str] = Field(default_factory=list)


class PricePoint(BaseModel):
    date: date
    close: float


class PriceResult(BaseModel):
    status: ResultStatus
    commodity: str
    requested_date: date
    observation: PricePoint | None = None
    symbol: str | None = None
    instrument: str | None = None
    currency: str | None = None
    unit: str | None = None
    provider: str | None = None
    is_proxy: bool = False
    source: SourceReference | None = None
    warnings: list[str] = Field(default_factory=list)


class TrendResult(BaseModel):
    status: ResultStatus
    commodity: str
    days: int
    points: list[PricePoint] = Field(default_factory=list)
    change_absolute: float | None = None
    change_percent: float | None = None
    direction: Literal["up", "down", "flat", "unknown"] = "unknown"
    symbol: str | None = None
    instrument: str | None = None
    currency: str | None = None
    unit: str | None = None
    provider: str | None = None
    is_proxy: bool = False
    source: SourceReference | None = None
    warnings: list[str] = Field(default_factory=list)


class AgentTraceEvent(BaseModel):
    phase: Literal["plan", "act", "observe", "render"]
    message: str


class MCPCallSummary(BaseModel):
    server: str
    label: str
    status: ResultStatus
    detail: str


class BriefingResult(BaseModel):
    markdown: str
    topic: str
    generated_at: datetime
    degraded: bool
    price_chart_svg: str | None = None
    trace: list[AgentTraceEvent] = Field(default_factory=list)
    mcp_calls: list[MCPCallSummary] = Field(default_factory=list)
