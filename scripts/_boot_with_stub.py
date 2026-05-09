"""Boot the FastAPI app with a stub extraction service for local smoke tests.

Not part of the production wheel. Used by `scripts/smoke_test.py` after a
fresh build, where we don't want to pay the cost (or risk) of talking to
real Bedrock.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import uvicorn

from bedrock_strands_agent.agent.builder import AgentBundle
from bedrock_strands_agent.agent.mcp import MCPManager
from bedrock_strands_agent.agent.prompts import PromptRenderer
from bedrock_strands_agent.api.app import create_app
from bedrock_strands_agent.config import Settings
from bedrock_strands_agent.extraction import ExtractionService

_CANNED = (
    '{"fields": ['
    '{"name": "invoice_number", "value": "INV-1", "confidence": 0.9},'
    '{"name": "invoice_date", "value": "2026-01-01", "confidence": 0.9},'
    '{"name": "vendor_name", "value": "Acme", "confidence": 0.9},'
    '{"name": "bill_to", "value": "Wile E.", "confidence": 0.9},'
    '{"name": "total", "value": 1, "confidence": 0.9}'
    "]}"
)


def _stub() -> ExtractionService:
    class _Resp:
        def __str__(self) -> str:
            return _CANNED

    agent: Any = MagicMock()
    agent.return_value = _Resp()
    s = Settings(mcp_config_path="mcp.config.json")
    return ExtractionService(
        AgentBundle(
            agent=agent,
            prompt_renderer=PromptRenderer(),
            mcp_manager=MCPManager(s.mcp_config_path, enabled_servers=[]),
            settings=s,
        )
    )


def app_factory() -> Any:
    """Return a FastAPI app wired to the stub service."""
    return create_app(extraction_service=_stub())


if __name__ == "__main__":
    uvicorn.run(app_factory(), host="127.0.0.1", port=8765, log_config=None)
