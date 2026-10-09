from __future__ import annotations

from pathlib import Path

import pytest

from mineral_daily.config import Settings, configure_logging, default_local_mcp_config


def test_settings_read_valid_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", "9")
    monkeypatch.setenv("PDF_MAX_BYTES", "1234")
    monkeypatch.setenv("ARTICLE_MAX_BYTES", "5678")
    monkeypatch.setenv("MINERAL_AGENT_LOG_LEVEL", "debug")

    settings = Settings.from_env()

    assert settings.http_timeout_seconds == 9
    assert settings.pdf_max_bytes == 1234
    assert settings.article_max_bytes == 5678
    assert settings.log_level == "DEBUG"


@pytest.mark.parametrize("value", ["zero", "0", "-1"])
def test_settings_reject_invalid_positive_integer(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("HTTP_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="HTTP_TIMEOUT_SECONDS"):
        Settings.from_env()


def test_default_config_prefers_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config = tmp_path / "mcp-config.local.json"
    config.write_text('{"mcpServers": {}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert default_local_mcp_config() == config


def test_configure_logging_accepts_named_level() -> None:
    configure_logging("INFO")
