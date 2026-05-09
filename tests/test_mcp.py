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
