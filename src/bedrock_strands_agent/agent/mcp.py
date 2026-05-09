"""MCP (Model Context Protocol) wiring.

`MCPManager` reads `mcp.config.json`, validates the entries the user opted
into via `MCP_ENABLED_SERVERS`, launches each over stdio, and exposes their
tools so the Strands agent can call them. Tool responses are passed through
`_SanitisingMCPTool` so a compromised or attacker-controlled MCP server
cannot paint unbounded text — including PII — into the agent's context
window. See ADR-0011.
"""

from __future__ import annotations

import json
import logging
from contextlib import ExitStack
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, override

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from strands.tools.mcp import MCPClient
from strands.tools.mcp.mcp_agent_tool import MCPAgentTool
from strands.types._events import ToolResultEvent

from bedrock_strands_agent.security.redaction import redact_for_logs

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from strands.types.tools import ToolResultContent, ToolUse

LOGGER = logging.getLogger(__name__)

# 8 KiB per individual MCP text-content block. Large enough for legit
# lookups (vendor metadata, schema descriptions, fetch summaries); small
# enough to bound the indirect-prompt-injection surface a compromised or
# malicious MCP server can paint into the agent's context window. See
# ADR-0011 ("MCP tool responses are unfiltered" deferral).
MCP_RESPONSE_TEXT_CAP: Final = 8 * 1024
_MCP_TRUNCATION_MARKER: Final = (
    "\n\n[TRUNCATED — MCP response exceeded {cap}-character cap; trailing content dropped]"
)


def _sanitise_mcp_text_blocks(content: list[ToolResultContent], cap: int) -> None:
    """In place: cap each text block's length and run it through redact_for_logs.

    Non-text content (image, document, json) is left untouched: capping
    image bytes would corrupt them, and redacting JSON would risk
    breaking caller-side parsers. Indirect prompt injection through
    image/document content is the responsibility of the upstream system
    that produced the MCP server's reply; the text-channel hardening
    covers the common `fetch` / `filesystem` / `vendor-lookup` shapes.
    """
    for block in content:
        if not isinstance(block, dict):  # pragma: no cover - TypedDict invariant
            continue
        text = block.get("text")
        if not isinstance(text, str):
            continue
        if len(text) > cap:
            text = text[:cap] + _MCP_TRUNCATION_MARKER.format(cap=cap)
        redacted = redact_for_logs(text)
        if redacted is not None:
            text = redacted
        block["text"] = text


class _SanitisingMCPTool(MCPAgentTool):
    """MCPAgentTool subclass that bounds tool-response text and redacts PII.

    Defence-in-depth against indirect prompt injection through MCP tool
    responses (ADR-0011). The MCP_ENABLED_SERVERS allowlist controls
    *which* servers run; this wrapper controls *what* their replies can
    look like once they're talking.
    """

    @override
    async def stream(
        self,
        tool_use: ToolUse,
        invocation_state: dict[str, Any],
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        """Sanitise text content blocks on every ``ToolResultEvent``."""
        async for event in super().stream(tool_use, invocation_state, **kwargs):
            if isinstance(event, ToolResultEvent):
                result = event.tool_result
                content = result.get("content") if isinstance(result, dict) else None
                if isinstance(content, list):
                    _sanitise_mcp_text_blocks(content, MCP_RESPONSE_TEXT_CAP)
            yield event


def _wrap_tools_with_sanitiser(tools: list[Any]) -> list[Any]:
    """Replace each MCPAgentTool with a `_SanitisingMCPTool` instance.

    Non-MCP tools (if any are present in the list) pass through unchanged.
    """
    out: list[Any] = []
    for tool in tools:
        if isinstance(tool, MCPAgentTool):
            out.append(
                _SanitisingMCPTool(
                    mcp_tool=tool.mcp_tool,
                    mcp_client=tool.mcp_client,
                    name_override=tool.tool_name,
                    timeout=tool.timeout,
                )
            )
        else:
            out.append(tool)
    return out


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
            tools = _wrap_tools_with_sanitiser(client.list_tools_sync())
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
