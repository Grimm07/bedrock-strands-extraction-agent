from __future__ import annotations

import pytest
from pydantic import ValidationError

from bedrock_strands_agent.config import Settings, get_settings, reset_settings_cache


def test_defaults() -> None:
    s = Settings()
    assert s.service_name == "bedrock-strands-agent"
    assert s.service_env == "local"
    assert s.log_level == "INFO"
    assert s.log_format == "json"
    assert s.api_port == 8000
    assert s.bedrock_model_id.startswith("us.anthropic.")
    assert s.mcp_enabled_servers == []


def test_csv_env_parsed_for_mcp_enabled_servers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_ENABLED_SERVERS", "filesystem, fetch ,, github")
    s = Settings()
    assert s.mcp_enabled_servers == ["filesystem", "fetch", "github"]


def test_aws_region_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    s = Settings()
    assert s.aws_region == "eu-west-1"


def test_invalid_log_level(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
    with pytest.raises(ValidationError):
        Settings()


def test_temperature_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BEDROCK_TEMPERATURE", "1.5")
    with pytest.raises(ValidationError):
        Settings()


def test_effective_otel_service_name_falls_back() -> None:
    s = Settings(service_name="x")
    assert s.effective_otel_service_name == "x"
    s2 = Settings(service_name="x", otel_service_name="y")
    assert s2.effective_otel_service_name == "y"


def test_is_otel_enabled_modes() -> None:
    assert Settings().is_otel_enabled is False
    assert Settings(strands_otel_enable_console_export=True).is_otel_enabled is True
    assert Settings(otel_exporter_otlp_endpoint="http://collector:4317").is_otel_enabled is True


def test_get_settings_is_cached() -> None:
    reset_settings_cache()
    a = get_settings()
    b = get_settings()
    assert a is b
