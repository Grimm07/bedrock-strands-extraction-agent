"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator

from bedrock_strands_agent import __version__
from bedrock_strands_agent.api.middleware import CorrelationIdMiddleware
from bedrock_strands_agent.api.routes import build_router
from bedrock_strands_agent.api.schemas import ErrorResponse
from bedrock_strands_agent.config import Settings, get_settings
from bedrock_strands_agent.extraction import ExtractionService
from bedrock_strands_agent.logging import configure_logging
from bedrock_strands_agent.telemetry import configure_tracing, instrument_fastapi

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

LOGGER = logging.getLogger(__name__)


def create_app(
    settings: Settings | None = None,
    *,
    extraction_service: ExtractionService | None = None,
) -> FastAPI:
    """Build and return a fully-wired FastAPI app.

    Args:
        settings: Optional pre-built `Settings`. If omitted, `get_settings()`
            is used.
        extraction_service: Optional pre-built `ExtractionService`. If omitted,
            one is constructed from `settings`. Tests pass a stub here so they
            never have to touch Bedrock.

    Returns:
        A FastAPI app ready to be served by uvicorn.
    """
    settings = settings or get_settings()
    configure_logging(settings)
    configure_tracing(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Lifespan hook: log startup, tear down MCP servers on shutdown."""
        LOGGER.info(
            "service starting",
            extra={"service": settings.service_name, "version": __version__},
        )
        try:
            yield
        finally:
            service = getattr(app.state, "extraction_service", None)
            bundle = getattr(service, "_bundle", None)
            mcp = getattr(bundle, "mcp_manager", None)
            if mcp is not None:
                mcp.stop()

    app = FastAPI(
        title=settings.service_name,
        version=__version__,
        description="Form-extraction agent built on Strands + Amazon Bedrock.",
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.extraction_service = extraction_service or ExtractionService.from_settings(settings)

    app.add_middleware(CorrelationIdMiddleware)
    app.include_router(build_router())

    Instrumentator().instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)
    instrument_fastapi(app)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Convert unhandled exceptions into a typed JSON error body."""
        LOGGER.exception("unhandled exception", exc_info=exc)
        body = ErrorResponse(
            detail="internal server error",
            correlation_id=getattr(request.state, "correlation_id", None),
        )
        return JSONResponse(status_code=500, content=body.model_dump())

    return app
