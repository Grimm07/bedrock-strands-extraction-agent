"""Build the Starlette routes that expose the A2A endpoint.

The list returned by :func:`build_a2a_routes` is intended to be spliced
into the existing FastAPI app in ``api/app.py`` so the A2A endpoint
inherits the same middleware (``CorrelationIdMiddleware``,
``AuthMiddleware``, slowapi) as ``/extract`` and ``/extract/document``.
We do NOT mount a separate Starlette sub-app; mounting would shadow the
parent middleware stack.

Three URLs are returned:

- ``GET /.well-known/agent-card.json`` — the canonical A2A discovery
  path. AgentCard is also served here for legacy clients via the
  a2a-sdk's deprecated-path compatibility shim.
- ``GET /a2a/.well-known/agent-card.json`` — namespaced copy for
  clients that look there.
- ``POST /a2a/jsonrpc`` — the JSON-RPC 2.0 endpoint that A2A uses for
  ``message/send`` and the rest of the protocol.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.server.apps import A2AStarletteApplication
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from starlette.responses import JSONResponse
from starlette.routing import Route

from bedrock_strands_agent.a2a.agent_card import build_agent_card
from bedrock_strands_agent.a2a.executor import ExtractionAgentExecutor

if TYPE_CHECKING:
    from a2a.types import AgentCard
    from starlette.requests import Request

    from bedrock_strands_agent.config import Settings
    from bedrock_strands_agent.extraction import ExtractionService


def build_a2a_routes(
    extraction_service: ExtractionService,
    settings: Settings,
) -> list[Route]:
    """Return the Starlette routes that expose this service over A2A.

    Args:
        extraction_service: The same `ExtractionService` instance the
            HTTP routes use. The A2A executor wraps it directly.
        settings: Application settings; used to build the AgentCard
            (service name, advertised URL, etc.).

    Returns:
        A list of Starlette `Route` objects. Splice into the FastAPI
        app's `routes` list so they inherit the parent middleware.
    """
    executor = ExtractionAgentExecutor(extraction_service)
    handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )
    agent_card = build_agent_card(settings)
    app = A2AStarletteApplication(agent_card=agent_card, http_handler=handler)
    canonical_routes = app.routes(rpc_url="/a2a/jsonrpc")
    return [*canonical_routes, _namespaced_agent_card_route(agent_card)]


def _namespaced_agent_card_route(agent_card: AgentCard) -> Route:
    """Build a route serving the agent card under ``/a2a/.well-known/``.

    Hand-rolled instead of reusing ``A2AStarletteApplication``'s
    private ``_handle_get_agent_card``: the SDK's public ``routes()``
    only emits the canonical card path, and binding the private method
    would break silently if the SDK renames it. The handler here matches
    the canonical path's response shape (``model_dump(mode='json',
    by_alias=True, exclude_none=True)`` is the a2a-sdk convention).
    """
    card_payload = agent_card.model_dump(mode="json", by_alias=True, exclude_none=True)

    async def _serve_namespaced_card(_request: Request) -> JSONResponse:
        return JSONResponse(card_payload)

    return Route(
        "/a2a/.well-known/agent-card.json",
        _serve_namespaced_card,
        methods=["GET"],
        name="namespaced_agent_card",
    )
