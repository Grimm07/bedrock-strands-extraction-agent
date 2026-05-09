"""HTTP routes for the extraction service."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Annotated, cast

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status

from bedrock_strands_agent import __version__
from bedrock_strands_agent.api.schemas import (
    ErrorResponse,
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
from bedrock_strands_agent.extraction.document import UnsupportedDocumentError
from bedrock_strands_agent.extraction.schemas import SCHEMA_REGISTRY

if TYPE_CHECKING:
    from slowapi import Limiter

    from bedrock_strands_agent.config import Settings

LOGGER = logging.getLogger(__name__)


def build_router(
    *,
    limiter: Limiter | None = None,
    rate_limit: str | None = None,
) -> APIRouter:
    """Build and return the API router.

    Args:
        limiter: Optional slowapi ``Limiter``. Required when ``rate_limit`` is
            set; ignored otherwise.
        rate_limit: Optional per-route rate-limit string (e.g. ``"60/minute"``)
            applied to ``/extract`` only. ``None`` leaves the route unbounded.
    """
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

    async def extract(body: ExtractRequestBody, request: Request) -> ExtractionResult:
        """Run an extraction against the named schema.

        ``ExtractionService.extract`` is sync (it wraps the Strands ``Agent``
        which we still drive synchronously); offload to a worker thread so
        the FastAPI event loop doesn't block on the Bedrock round-trip.
        """
        service = cast("ExtractionService", request.app.state.extraction_service)
        correlation_id = getattr(request.state, "correlation_id", None)
        try:
            return await asyncio.to_thread(
                service.extract,
                document_text=body.document_text,
                schema_name=body.schema_name,
                schema_version=body.schema_version,
                document_id=body.document_id,
                correlation_id=correlation_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ExtractionError as exc:
            LOGGER.exception("extraction failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    extract_endpoint = (
        limiter.limit(rate_limit)(extract) if (limiter is not None and rate_limit) else extract
    )
    router.add_api_route(
        "/extract",
        extract_endpoint,
        methods=["POST"],
        response_model=ExtractionResult,
        response_model_by_alias=True,
        responses={
            404: {"model": ErrorResponse, "description": "Unknown schema_name"},
            502: {"model": ErrorResponse, "description": "Upstream Bedrock failure"},
        },
        tags=["extraction"],
    )

    async def extract_document(
        request: Request,
        schema_name: Annotated[str, Form(min_length=1)],
        file: Annotated[UploadFile, File(description="PDF or image (PNG/JPEG/WebP/GIF) upload")],
        document_id: Annotated[str | None, Form()] = None,
        schema_version: Annotated[
            str | None,
            Form(description="Optional pin to a specific schema version (see /extract)."),
        ] = None,
    ) -> ExtractionResult:
        """Run an extraction against an uploaded document (auto-routes text vs vision)."""
        service = cast("ExtractionService", request.app.state.extraction_service)
        correlation_id = getattr(request.state, "correlation_id", None)
        contents = await file.read()
        try:
            return await asyncio.to_thread(
                service.extract_document,
                upload_bytes=contents,
                content_type=file.content_type or "application/octet-stream",
                schema_name=schema_name,
                schema_version=schema_version,
                document_id=document_id,
                correlation_id=correlation_id,
            )
        except UnsupportedDocumentError as exc:
            # Starlette ≥ 0.49 renamed HTTP_422_UNPROCESSABLE_ENTITY →
            # HTTP_422_UNPROCESSABLE_CONTENT; use the literal so the route
            # works on both with no DeprecationWarning leak.
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ExtractionError as exc:
            LOGGER.exception("document extraction failed")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    extract_document_endpoint = (
        limiter.limit(rate_limit)(extract_document)
        if (limiter is not None and rate_limit)
        else extract_document
    )
    router.add_api_route(
        "/extract/document",
        extract_document_endpoint,
        methods=["POST"],
        response_model=ExtractionResult,
        response_model_by_alias=True,
        responses={
            404: {"model": ErrorResponse, "description": "Unknown schema_name"},
            422: {
                "model": ErrorResponse,
                "description": "Unsupported document type or unreadable file",
            },
            502: {"model": ErrorResponse, "description": "Upstream Bedrock failure"},
        },
        tags=["extraction"],
    )

    return router
