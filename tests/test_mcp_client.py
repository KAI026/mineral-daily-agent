from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from mineral_daily.agent import mcp_client as module
from mineral_daily.agent.mcp_client import MCPPool, MCPToolError


@pytest.mark.asyncio
async def test_invalid_mcp_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text("{}", encoding="utf-8")

    with pytest.raises(MCPToolError, match="invalid MCP config"):
        async with MCPPool(path):
            pass


@pytest.mark.asyncio
async def test_pool_connects_and_unwraps_structured_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    class FakeClient:
        def __init__(self, parameters: object) -> None:
            self.parameters = parameters

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            return None

        async def call_tool(self, *args: object, **kwargs: object) -> Any:
            return SimpleNamespace(
                is_error=False,
                structured_content={"result": {"status": "ok", "value": 1}},
                content=[],
            )

    monkeypatch.setattr(module, "Client", FakeClient)
    path = tmp_path / "mcp.json"
    path.write_text(
        json.dumps({"mcpServers": {"test": {"command": "${PYTHON}", "args": ["-m", "x"]}}}),
        encoding="utf-8",
    )

    async with MCPPool(path) as pool:
        payload = await pool.call("test", "ping", {})

    assert payload == {"status": "ok", "value": 1}


@pytest.mark.asyncio
async def test_call_rejects_unknown_server(tmp_path: Path) -> None:
    path = tmp_path / "mcp.json"
    path.write_text(json.dumps({"mcpServers": {"x": {"command": "x"}}}), encoding="utf-8")
    pool = MCPPool(path)

    with pytest.raises(MCPToolError, match="not connected"):
        await pool.call("missing", "ping", {})
