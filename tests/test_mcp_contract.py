from __future__ import annotations

import sys

import pytest
from mcp import Client, StdioServerParameters

from mineral_daily.servers import news, pdf, price

PILBARA_PDF = "https://announcements.asx.com.au/asxpdf/20250611/pdf/06kmc3l7r1bjsm.pdf"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("server", "expected"),
    [
        (news.mcp, {"search", "fetch_article"}),
        (pdf.mcp, {"extract_resources"}),
        (price.mcp, {"get_price", "get_trend"}),
    ],
)
async def test_in_process_mcp_tool_contract(server: object, expected: set[str]) -> None:
    async with Client(server) as client:  # type: ignore[arg-type]
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("module", "expected"),
    [
        ("mineral_daily.servers.news", {"search", "fetch_article"}),
        ("mineral_daily.servers.pdf", {"extract_resources"}),
        ("mineral_daily.servers.price", {"get_price", "get_trend"}),
    ],
)
async def test_real_stdio_transport(module: str, expected: set[str]) -> None:
    parameters = StdioServerParameters(command=sys.executable, args=["-m", module])
    async with Client(parameters) as client:
        result = await client.list_tools()

    assert {tool.name for tool in result.tools} == expected


@pytest.mark.asyncio
async def test_pdf_tool_returns_structured_snapshot_over_mcp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINERAL_AGENT_PREFER_SNAPSHOTS", "1")

    async with Client(pdf.mcp) as client:
        result = await client.call_tool(
            "extract_resources", {"pdf_url": PILBARA_PDF, "page_numbers": [2]}
        )

    assert result.is_error is False
    assert result.structured_content is not None
    assert result.structured_content["report_standard"] == "JORC"
    assert len(result.structured_content["records"]) == 3
