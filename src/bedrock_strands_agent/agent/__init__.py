"""Strands agent layer: prompts, tools, MCP integration, and assembly."""

from bedrock_strands_agent.agent.builder import AgentBundle, build_agent
from bedrock_strands_agent.agent.mcp import MCPConfigError, MCPManager
from bedrock_strands_agent.agent.prompts import PromptRenderer
from bedrock_strands_agent.agent.tools import DEFAULT_TOOLS

__all__ = [
    "DEFAULT_TOOLS",
    "AgentBundle",
    "MCPConfigError",
    "MCPManager",
    "PromptRenderer",
    "build_agent",
]
