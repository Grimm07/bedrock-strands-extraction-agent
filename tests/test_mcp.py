from __future__ import annotations

import json
from pathlib import Path

import pytest

from bedrock_strands_agent.agent.mcp import MCPConfigError, MCPManager


def _write(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "mcp.config.json"
    p.write_text(content, encoding="utf-8")
    return p


def test_no_servers_enabled_starts_clean(tmp_path: Path) -> None:
    cfg = _write(tmp_path, json.dumps({"mcpServers": {}}))
    m = MCPManager(cfg, enabled_servers=[])
    m.start()
    assert m.tools == []
    m.stop()


def test_enabled_server_missing_in_config(tmp_path: Path) -> None:
    cfg = _write(tmp_path, json.dumps({"mcpServers": {}}))
    m = MCPManager(cfg, enabled_servers=["filesystem"])
    with pytest.raises(MCPConfigError, match="not in"):
        m.start()


def test_missing_config_file(tmp_path: Path) -> None:
    m = MCPManager(tmp_path / "missing.json", enabled_servers=["any"])
    with pytest.raises(MCPConfigError, match="not found"):
        m.start()


def test_invalid_json(tmp_path: Path) -> None:
    cfg = _write(tmp_path, "{not valid json")
    m = MCPManager(cfg, enabled_servers=["any"])
    with pytest.raises(MCPConfigError, match="invalid JSON"):
        m.start()


def test_top_level_not_object(tmp_path: Path) -> None:
    cfg = _write(tmp_path, json.dumps([1, 2, 3]))
    m = MCPManager(cfg, enabled_servers=["any"])
    with pytest.raises(MCPConfigError, match="must contain a JSON object"):
        m.start()


def test_unsupported_transport(tmp_path: Path) -> None:
    cfg = _write(
        tmp_path,
        json.dumps({"mcpServers": {"ws": {"transport": "websocket", "command": "x", "args": []}}}),
    )
    m = MCPManager(cfg, enabled_servers=["ws"])
    with pytest.raises(MCPConfigError, match="unsupported transport"):
        m.start()


def test_missing_command(tmp_path: Path) -> None:
    cfg = _write(tmp_path, json.dumps({"mcpServers": {"x": {"transport": "stdio", "args": []}}}))
    m = MCPManager(cfg, enabled_servers=["x"])
    with pytest.raises(MCPConfigError, match="missing 'command'"):
        m.start()


# --------------------------------------------------------------------------- #
# MCP response sanitiser — defence-in-depth for indirect prompt injection
# --------------------------------------------------------------------------- #


def test_sanitiser_truncates_text_blocks_at_cap() -> None:
    """Text blocks longer than `MCP_RESPONSE_TEXT_CAP` get truncated.

    A compromised or attacker-controlled MCP server (e.g., a `fetch` tool
    pulling adversarial HTML) could otherwise paint unbounded text into
    the agent's context window. ADR-0011.
    """
    from bedrock_strands_agent.agent.mcp import (
        MCP_RESPONSE_TEXT_CAP,
        _sanitise_mcp_text_blocks,
    )

    long_text = "x" * (MCP_RESPONSE_TEXT_CAP + 5_000)
    blocks: list[dict[str, object]] = [{"text": long_text}]
    _sanitise_mcp_text_blocks(blocks, MCP_RESPONSE_TEXT_CAP)
    text = blocks[0]["text"]
    assert isinstance(text, str)
    assert len(text) <= MCP_RESPONSE_TEXT_CAP + 200, (
        "truncation marker should be small overhead on top of the cap"
    )
    assert "TRUNCATED" in text
    assert text.startswith("x" * 100)


def test_sanitiser_redacts_pii_in_text_blocks() -> None:
    """If an MCP tool response contains PII tokens, they are redacted.

    The same redaction patterns that protect logs apply here so a
    compromised vendor-lookup tool can't inject e.g. a fake SSN or
    AWS key into the model's context window.
    """
    from bedrock_strands_agent.agent.mcp import (
        MCP_RESPONSE_TEXT_CAP,
        _sanitise_mcp_text_blocks,
    )

    blocks: list[dict[str, object]] = [
        {"text": "vendor lookup says SSN 123-45-6789 owns the account"}
    ]
    _sanitise_mcp_text_blocks(blocks, MCP_RESPONSE_TEXT_CAP)
    out = blocks[0]["text"]
    assert isinstance(out, str)
    assert "[REDACTED-SSN]" in out
    assert "123-45-6789" not in out


def test_sanitiser_leaves_non_text_blocks_alone() -> None:
    """Non-text content (image, document, json) must pass through unchanged.

    Capping image bytes would corrupt them; redacting JSON values would
    risk breaking caller-side parsers. Indirect prompt injection through
    image/document content is the responsibility of the upstream
    producer, not this layer.
    """
    from bedrock_strands_agent.agent.mcp import (
        MCP_RESPONSE_TEXT_CAP,
        _sanitise_mcp_text_blocks,
    )

    blocks: list[dict[str, object]] = [
        {"image": {"format": "png", "source": {"bytes": b"\x89PNG..."}}},
        {"json": {"vendor": "acme", "ssn": "123-45-6789"}},  # JSON values not redacted.
    ]
    before = [dict(b) for b in blocks]
    _sanitise_mcp_text_blocks(blocks, MCP_RESPONSE_TEXT_CAP)
    assert blocks == before


def test_sanitiser_passes_short_clean_text_through_unchanged() -> None:
    """Short benign text is a no-op pass-through (no false positives)."""
    from bedrock_strands_agent.agent.mcp import (
        MCP_RESPONSE_TEXT_CAP,
        _sanitise_mcp_text_blocks,
    )

    blocks: list[dict[str, object]] = [{"text": "vendor: acme widgets"}]
    _sanitise_mcp_text_blocks(blocks, MCP_RESPONSE_TEXT_CAP)
    assert blocks[0]["text"] == "vendor: acme widgets"


def test_wrap_tools_with_sanitiser_preserves_non_mcp_tools() -> None:
    """Non-MCP tools (built-in `@tool`-decorated functions, etc.) must be
    untouched by the wrapper so the rest of the agent's toolset still works.
    """
    from bedrock_strands_agent.agent.mcp import _wrap_tools_with_sanitiser

    sentinel = object()
    out = _wrap_tools_with_sanitiser([sentinel])
    assert out == [sentinel]
