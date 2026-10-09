from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, cast


def _positive_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


@dataclass(frozen=True, slots=True)
class Settings:
    http_timeout_seconds: int
    pdf_max_bytes: int
    article_max_bytes: int
    user_agent: str
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            http_timeout_seconds=_positive_int("HTTP_TIMEOUT_SECONDS", 20),
            pdf_max_bytes=_positive_int("PDF_MAX_BYTES", 25 * 1024 * 1024),
            article_max_bytes=_positive_int("ARTICLE_MAX_BYTES", 3 * 1024 * 1024),
            user_agent=os.getenv(
                "MINERAL_AGENT_USER_AGENT",
                "mineral-daily-agent/0.1 (+https://github.com/)",
            ),
            log_level=os.getenv("MINERAL_AGENT_LOG_LEVEL", "INFO").upper(),
        )


def configure_logging(level: str | None = None) -> None:
    selected = level or Settings.from_env().log_level
    numeric = getattr(logging, selected, logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def load_project_registry() -> dict[str, dict[str, Any]]:
    path = files("mineral_daily.fixtures").joinpath("projects.json")
    document = json.loads(path.read_text(encoding="utf-8"))
    return cast(dict[str, dict[str, Any]], document)


def default_local_mcp_config() -> Path:
    candidate = Path.cwd() / "mcp-config.local.json"
    if candidate.exists():
        return candidate
    package_root = Path(__file__).resolve().parents[2]
    candidate = package_root / "mcp-config.local.json"
    if candidate.exists():
        return candidate
    raise FileNotFoundError(
        "mcp-config.local.json was not found; pass --config with an explicit path"
    )
