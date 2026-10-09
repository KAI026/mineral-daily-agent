from __future__ import annotations

import json
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from types import TracebackType
from typing import Any

from mcp import Client, StdioServerParameters


class MCPToolError(RuntimeError):
    """A configured MCP server failed or returned a tool-level error."""


class MCPPool:
    def __init__(self, config_path: Path) -> None:
        self.config_path = config_path
        self._stack = AsyncExitStack()
        self._clients: dict[str, Client] = {}
        self._tool_timeout = float(os.getenv("MCP_TOOL_TIMEOUT_SECONDS", "60"))
        if self._tool_timeout <= 0:
            raise MCPToolError("MCP_TOOL_TIMEOUT_SECONDS must be positive")

    async def __aenter__(self) -> MCPPool:
        try:
            document = json.loads(self.config_path.read_text(encoding="utf-8"))
            servers = document["mcpServers"]
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise MCPToolError(f"invalid MCP config {self.config_path}: {exc}") from exc
        if not isinstance(servers, dict) or not servers:
            raise MCPToolError("MCP config must define a non-empty mcpServers object")
        try:
            for name, raw in servers.items():
                if not isinstance(raw, dict) or not isinstance(raw.get("command"), str):
                    raise MCPToolError(f"invalid server configuration: {name}")
                configured_env = raw.get("env") or {}
                if not isinstance(configured_env, dict):
                    raise MCPToolError(f"env for {name} must be an object")
                environment = {str(key): str(value) for key, value in configured_env.items()}
                # Explicitly forward only operational settings; secrets are neither required nor logged.
                for key in (
                    "HTTP_TIMEOUT_SECONDS",
                    "MCP_TOOL_TIMEOUT_SECONDS",
                    "PDF_MAX_BYTES",
                    "ARTICLE_MAX_BYTES",
                    "MINERAL_AGENT_LOG_LEVEL",
                    "MINERAL_AGENT_PREFER_SNAPSHOTS",
                    "HTTP_PROXY",
                    "HTTPS_PROXY",
                    "NO_PROXY",
                ):
                    if key in os.environ:
                        environment[key] = os.environ[key]
                command = sys.executable if raw["command"] == "${PYTHON}" else raw["command"]
                parameters = StdioServerParameters(
                    command=command,
                    args=[str(item) for item in raw.get("args", [])],
                    env=environment or None,
                    cwd=raw.get("cwd"),
                )
                client = Client(parameters)
                self._clients[str(name)] = await self._stack.enter_async_context(client)
        except BaseException:
            await self._stack.aclose()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._stack.__aexit__(exc_type, exc, traceback)

    async def call(self, server: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        client = self._clients.get(server)
        if client is None:
            raise MCPToolError(f"server is not connected: {server}")
        result = await client.call_tool(tool, arguments, read_timeout_seconds=self._tool_timeout)
        if result.is_error:
            detail = " ; ".join(getattr(item, "text", str(item)) for item in result.content)
            raise MCPToolError(f"{server}.{tool} failed: {detail}")
        payload = result.structured_content
        if isinstance(payload, dict):
            if set(payload) == {"result"} and isinstance(payload["result"], dict):
                return payload["result"]
            return payload
        for item in result.content:
            text = getattr(item, "text", None)
            if isinstance(text, str):
                try:
                    parsed = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        raise MCPToolError(f"{server}.{tool} did not return structured JSON")
