"""A2A (agent-to-agent) protocol integration.

When `Settings.a2a_enabled` is true, the FastAPI app mounts an A2A
JSON-RPC endpoint at `/a2a/jsonrpc` and the agent-card discovery
document at the canonical `/.well-known/agent-card.json` (plus a copy
under `/a2a/.well-known/agent-card.json` for clients that namespace
discovery). Other A2A-compliant agents can then call this service for
schema-driven document extraction.

The integration deliberately wraps `ExtractionService` directly rather
than using the Strands `StrandsA2AExecutor`. The schema-first contract,
validators, citation verification, retry loop, and vision-mode
grounding (ADR-0011) are all load-bearing; surfacing the agent's
free-text Bedrock response over A2A would lose them. Instead, an A2A
caller sends a structured `DataPart` payload with `schema_name` +
`document_text`, and receives the validated `ExtractionResult` back as
a `DataPart` artifact.

The public API is `build_a2a_routes(extraction_service, settings)`,
which returns a list of Starlette `Route` objects ready to splice into
the FastAPI app. The lower-level executor and agent-card builder are
also exported for tests and future tooling.
"""

from __future__ import annotations

from bedrock_strands_agent.a2a.agent_card import build_agent_card
from bedrock_strands_agent.a2a.executor import ExtractionAgentExecutor
from bedrock_strands_agent.a2a.routes import build_a2a_routes

__all__ = [
    "ExtractionAgentExecutor",
    "build_a2a_routes",
    "build_agent_card",
]
