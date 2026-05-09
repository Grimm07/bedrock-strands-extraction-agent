"""Shared pytest fixtures.

Two non-obvious things happen here:

1. `_isolated_env` (autouse) clears AWS_*, OTEL_*, and MCP_* env vars before
   every test so a developer's shell doesn't leak into the suite.
2. `stub_extraction_service` builds an `ExtractionService` whose underlying
   `AgentBundle.agent` is a MagicMock returning canned JSON. No test in the
   suite ever talks to Bedrock.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from bedrock_strands_agent.agent.builder import AgentBundle
from bedrock_strands_agent.agent.mcp import MCPManager
from bedrock_strands_agent.agent.prompts import PromptRenderer
from bedrock_strands_agent.config import Settings, reset_settings_cache
from bedrock_strands_agent.extraction import ExtractionService
from bedrock_strands_agent.telemetry import reset_for_tests as _reset_telemetry

_LEAKY_PREFIXES = (
    "AWS_",
    "OTEL_",
    "STRANDS_OTEL_",
    "MCP_",
    "BEDROCK_",
    "AUTH_",
    "JWT_",
    "API_KEYS",
    "RATE_LIMIT_",
)


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear every env var that would influence settings."""
    for key in list(os.environ):
        if key.startswith(_LEAKY_PREFIXES):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    monkeypatch.delenv("LOG_FORMAT", raising=False)
    reset_settings_cache()
    _reset_telemetry()


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """Local-dev defaults pointing the MCP config at a known empty file."""
    cfg = tmp_path / "mcp.config.json"
    cfg.write_text('{"mcpServers": {}}', encoding="utf-8")
    return Settings(
        service_name="bedrock-strands-agent",
        service_env="local",
        log_level="WARNING",
        log_format="text",
        bedrock_model_id="us.anthropic.claude-sonnet-4-6-20250514-v1:0",
        mcp_config_path=cfg,
    )


def _make_canned_response(payload: str) -> Any:
    """Wrap a string so the service's `__str__`/`.text` paths both work."""

    class _Resp:
        text = payload

        def __str__(self) -> str:
            return payload

    return _Resp()


_CANNED_INVOICE_JSON = (
    '{"fields": ['
    '{"name": "invoice_number", "value": "INV-001", "confidence": 0.95, '
    '"source_excerpt": "INVOICE #INV-001"},'
    '{"name": "invoice_date", "value": "2026-04-15", "confidence": 0.9, '
    '"source_excerpt": "Date: 2026-04-15"},'
    '{"name": "vendor_name", "value": "Acme", "confidence": 0.9, '
    '"source_excerpt": "Acme"},'
    '{"name": "bill_to", "value": "Wile E. Coyote", "confidence": 0.85, '
    '"source_excerpt": "Bill to: Wile E. Coyote"},'
    '{"name": "total", "value": 100, "confidence": 0.95, '
    '"source_excerpt": "Total: $100"}'
    "]}"
)


@pytest.fixture
def stub_extraction_service(settings: Settings, tmp_path: Path) -> ExtractionService:
    """An `ExtractionService` whose agent returns a canned successful payload."""
    agent = MagicMock()
    agent.return_value = _make_canned_response(_CANNED_INVOICE_JSON)
    bundle = AgentBundle(
        agent=agent,
        prompt_renderer=PromptRenderer(),
        mcp_manager=MCPManager(config_path=settings.mcp_config_path, enabled_servers=[]),
        settings=settings,
    )
    return ExtractionService(bundle, max_retries=1)


@pytest.fixture
def stub_streaming_service(settings: Settings, tmp_path: Path) -> ExtractionService:
    """An `ExtractionService` whose agent.stream_async yields canned text deltas.

    Mirrors the Strands streaming event shape: each delta is a dict with a
    ``data`` key carrying the text chunk; the service collects those into
    the JSON payload and validates it at end-of-stream.
    """
    chunks = (
        '{"fields": [',
        '{"name": "invoice_number", "value": "INV-001", "confidence": 0.95, ',
        '"source_excerpt": "INVOICE #INV-001"},',
        '{"name": "invoice_date", "value": "2026-04-15", "confidence": 0.9, ',
        '"source_excerpt": "Date: 2026-04-15"},',
        '{"name": "vendor_name", "value": "Acme", "confidence": 0.9, ',
        '"source_excerpt": "Acme"},',
        '{"name": "bill_to", "value": "Wile E. Coyote", "confidence": 0.85, ',
        '"source_excerpt": "Bill to"},',
        '{"name": "total", "value": 100, "confidence": 0.95, ',
        '"source_excerpt": "Total"}',
        "]}",
    )

    async def _fake_stream_async(_prompt: str) -> Any:
        # Strands wraps text deltas in {"data": ...}; other event keys
        # (init_event_loop, contentBlockStop, result, etc.) are ignored
        # by the service so we don't bother to replicate them.
        for c in chunks:
            yield {"data": c}

    agent = MagicMock()
    agent.stream_async = _fake_stream_async
    bundle = AgentBundle(
        agent=agent,
        prompt_renderer=PromptRenderer(),
        mcp_manager=MCPManager(config_path=settings.mcp_config_path, enabled_servers=[]),
        settings=settings,
    )
    return ExtractionService(bundle, max_retries=1)


@pytest.fixture
def stub_streaming_service_returns_garbage(settings: Settings, tmp_path: Path) -> ExtractionService:
    """Streaming service whose model emits non-JSON garbage.

    Used to exercise the JSON-parse-failure branch of `extract_stream`,
    which must surface a terminal `event: error` frame rather than a
    `event: result`.
    """

    async def _fake_stream_async(_prompt: str) -> Any:
        for c in ("not ", "valid ", "json ", "at all"):
            yield {"data": c}

    agent = MagicMock()
    agent.stream_async = _fake_stream_async
    bundle = AgentBundle(
        agent=agent,
        prompt_renderer=PromptRenderer(),
        mcp_manager=MCPManager(config_path=settings.mcp_config_path, enabled_servers=[]),
        settings=settings,
    )
    return ExtractionService(bundle, max_retries=1)
