"""MCP (Model Context Protocol) wiring.

`MCPManager` reads `mcp.config.json`, validates the entries the user opted
into via `MCP_ENABLED_SERVERS`, launches each over stdio, and exposes their
tools so the Strands agent can call them.
"""

from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands.tools.mcp import MCPClient

LOGGER = logging.getLogger(__name__)


class MCPConfigError(ValueError):
    """Raised when `mcp.config.json` is malformed or references a missing entry."""


class MCPManager:
    """Lifecycle owner for any enabled MCP servers."""

    def __init__(self, config_path: Path, enabled_servers: list[str]) -> None:
        """Construct without starting any servers.

        Args:
            config_path: Path to `mcp.config.json`.
            enabled_servers: Names of servers in the config to start.
        """
        self._config_path = config_path
        self._enabled = list(enabled_servers)
        self._stack = ExitStack()
        self._tools: list[object] = []
        self._started = False

    @property
    def tools(self) -> list[object]:
        """Tools surfaced by the started MCP servers."""
        return list(self._tools)

    def start(self) -> None:
        """Load the config and start every enabled server."""
        if self._started:
            return
        if not self._enabled:
            self._started = True
            return

        config = self._load_config()
        servers = config.get("mcpServers")
        if not isinstance(servers, dict):
            raise MCPConfigError("mcp.config.json must define an 'mcpServers' object")

        for name in self._enabled:
            entry = servers.get(name)
            if entry is None:
                raise MCPConfigError(
                    f"MCP server {name!r} is enabled but not in {self._config_path}"
                )
            client = self._build_client(name, entry)
            self._stack.enter_context(client)
            tools = client.list_tools_sync()
            self._tools.extend(tools)
            LOGGER.info("MCP server %s started with %d tool(s)", name, len(tools))
        self._started = True

    def stop(self) -> None:
        """Shut down every MCP server."""
        self._stack.close()
        self._tools.clear()
        self._started = False

    # ------------------------------------------------------------------ #
    def _load_config(self) -> dict[str, Any]:
        if not self._config_path.exists():
            raise MCPConfigError(f"{self._config_path} not found")
        try:
            data = json.loads(self._config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise MCPConfigError(f"invalid JSON in {self._config_path}: {exc}") from exc
        if not isinstance(data, dict):
            raise MCPConfigError(f"{self._config_path} must contain a JSON object")
        return data

    @staticmethod
    def _build_client(name: str, entry: dict[str, Any]) -> MCPClient:
        transport = entry.get("transport", "stdio")
        if transport != "stdio":
            raise MCPConfigError(f"MCP server {name!r} uses unsupported transport {transport!r}")
        command = entry.get("command")
        if not isinstance(command, str) or not command:
            raise MCPConfigError(f"MCP server {name!r} is missing 'command'")
        args_node = entry.get("args", [])
        if not isinstance(args_node, list):
            raise MCPConfigError(f"MCP server {name!r}.args must be a list")
        env_node = entry.get("env", {})
        if not isinstance(env_node, dict):
            raise MCPConfigError(f"MCP server {name!r}.env must be an object")

        params = StdioServerParameters(
            command=command,
            args=[str(a) for a in args_node],
            env={str(k): str(v) for k, v in env_node.items()} or None,
        )

        # A named nested function avoids mypy's "Cannot infer type of lambda".
        def _connect(p: StdioServerParameters = params) -> Any:
            return stdio_client(p)

        return MCPClient(_connect)

    def __enter__(self) -> MCPManager:
        """Start the manager; returns `self` for `with` usage."""
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Stop the manager on context exit."""
        self.stop()
