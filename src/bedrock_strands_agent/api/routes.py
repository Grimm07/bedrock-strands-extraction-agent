"""HTTP routes for the extraction service."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Request, status

from bedrock_strands_agent import __version__
from bedrock_strands_agent.api.schemas import (
    ExtractRequestBody,
    HealthResponse,
    SchemasResponse,
    SchemaSummary,
)
from bedrock_strands_agent.extraction import (
    ExtractionError,
    ExtractionResult,
    ExtractionService,
)
from bedrock_strands_agent.extraction.schemas import SCHEMA_REGISTRY

if TYPE_CHECKING:
    from bedrock_strands_agent.config import Settings

LOGGER = logging.getLogger(__name__)


def build_router() -> APIRouter:
    """Build and return the API router."""
    router = APIRouter()

    @router.get("/health", response_model=HealthResponse, tags=["meta"])
    async def health(request: Request) -> HealthResponse:
        """Liveness probe."""
        settings = cast("Settings", request.app.state.settings)
        return HealthResponse(service=settings.service_name, version=__version__)

    @router.get("/schemas", response_model=SchemasResponse, tags=["meta"])
    async def list_schemas() -> SchemasResponse:
        """Return every registered schema (name, version, field count)."""
        items = [
            SchemaSummary(
                name=s.name,
                version=s.version,
                description=s.description,
                field_count=len(s.fields),
                required_fields=[f.name for f in s.fields if f.required],
            )
            for s in sorted(SCHEMA_REGISTRY.values(), key=lambda s: s.name)
        ]
        return SchemasResponse(schemas=items)

    @router.post(
        "/extract",
        response_model=ExtractionResult,
        response_model_by_alias=True,
        tags=["extraction"],
    )
    async def extract(body: ExtractRequestBody, request: Request) -> ExtractionResult:
        """Run an extraction against the named schema."""
        service = cast("ExtractionService", request.app.state.extraction_service)
        correlation_id = getattr(request.state, "correlation_id", None)
        try:
            return service.extract(
                document_text=body.document_text,
                schema_name=body.schema_name,
                document_id=body.document_id,
                correlation_id=correlation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ExtractionError as exc:
            LOGGER.exception("extraction failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return router
