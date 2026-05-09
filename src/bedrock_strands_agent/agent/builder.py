"""Compose the Strands agent from settings, tools, MCP servers, and prompts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from strands import Agent
from strands.models.bedrock import BedrockModel

from bedrock_strands_agent.agent.mcp import MCPManager
from bedrock_strands_agent.agent.prompts import PromptRenderer
from bedrock_strands_agent.agent.tools import DEFAULT_TOOLS

if TYPE_CHECKING:
    from bedrock_strands_agent.config import Settings


@dataclass(slots=True)
class AgentBundle:
    """A pre-assembled Strands agent and its collaborators.

    Attributes:
        agent: The Strands `Agent` ready to be called with a prompt string.
        prompt_renderer: The Jinja-based prompt renderer.
        mcp_manager: The (possibly empty) MCP manager owning subprocess lifecycles.
        settings: The settings the bundle was built from.
    """

    agent: Agent
    prompt_renderer: PromptRenderer
    mcp_manager: MCPManager
    settings: Settings


def build_agent(settings: Settings, *, extra_tools: list[Any] | None = None) -> AgentBundle:
    """Assemble an `AgentBundle` from settings.

    Args:
        settings: Application settings.
        extra_tools: Optional list of additional tools to expose to the agent.

    Returns:
        An `AgentBundle` whose `agent` is ready to be invoked.
    """
    model = BedrockModel(
        model_id=settings.bedrock_model_id,
        region_name=settings.aws_region,
        max_tokens=settings.bedrock_max_tokens,
        temperature=settings.bedrock_temperature,
        top_p=settings.bedrock_top_p,
    )
    mcp_manager = MCPManager(
        config_path=settings.mcp_config_path,
        enabled_servers=settings.mcp_enabled_servers,
    )
    mcp_manager.start()

    renderer = PromptRenderer()
    system_prompt = renderer.system(settings=settings)

    tools: list[Any] = [*DEFAULT_TOOLS, *mcp_manager.tools]
    if extra_tools:
        tools.extend(extra_tools)

    agent = Agent(model=model, tools=tools, system_prompt=system_prompt)
    return AgentBundle(
        agent=agent,
        prompt_renderer=renderer,
        mcp_manager=mcp_manager,
        settings=settings,
    )
