"""A2A AgentCard discovery document for the extraction service."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.types import AgentCapabilities, AgentCard, AgentSkill

from bedrock_strands_agent import __version__

if TYPE_CHECKING:
    from bedrock_strands_agent.config import Settings


_DEFAULT_DESCRIPTION = (
    "Schema-driven document field extraction over Amazon Bedrock. "
    "Send a DataPart with `schema_name` (required), `document_text` "
    "(required), and optionally `schema_version` (pin to a registered "
    "version) and `document_id` (caller-side correlation id). Receive "
    "a validated `ExtractionResult` back as a DataPart artifact, with "
    "per-field values, model self-confidence, citation flags, and "
    "warnings. Available schemas can be discovered out-of-band via the "
    "service's HTTP `/schemas` endpoint."
)

_EXTRACT_SKILL_DESCRIPTION = (
    "Extract structured fields from a document according to a "
    "registered FormSchema. Schema-first contract: the response shape "
    "is fixed by the schema, not by the model. Failures are surfaced "
    "as A2A task failures with structured error messages."
)


def build_agent_card(settings: Settings) -> AgentCard:
    """Build the A2A AgentCard advertising this service's capabilities.

    The card is served at `/.well-known/agent-card.json` (the A2A
    canonical discovery path) and mirrored under
    `/a2a/.well-known/agent-card.json` for clients that namespace
    discovery. The advertised `url` is the JSON-RPC endpoint where
    other agents send `message/send` requests.
    """
    public_url = settings.a2a_public_url
    if not public_url:
        public_url = f"http://{settings.api_host}:{settings.api_port}/a2a/jsonrpc"

    return AgentCard(
        name=settings.service_name,
        description=_DEFAULT_DESCRIPTION,
        url=public_url,
        version=__version__,
        protocol_version="0.3.0",
        default_input_modes=["application/json"],
        default_output_modes=["application/json"],
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
            state_transition_history=False,
        ),
        skills=[
            AgentSkill(
                id="extract",
                name="extract",
                description=_EXTRACT_SKILL_DESCRIPTION,
                tags=["extraction", "document", "structured-data", "schema-first"],
                input_modes=["application/json"],
                output_modes=["application/json"],
                examples=[
                    'Send a DataPart with {"schema_name":"invoice","document_text":"INVOICE #..."} '
                    "to extract invoice fields.",
                    'Pin a schema version: {"schema_name":"irs_w9","schema_version":"1.0.0",'
                    '"document_text":"..."}.',
                ],
            ),
        ],
    )
