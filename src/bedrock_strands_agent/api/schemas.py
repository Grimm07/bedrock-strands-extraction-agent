"""Pydantic models for HTTP request/response bodies."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HealthResponse(BaseModel):
    """`/health` response body."""

    status: str = Field(default="ok")
    service: str
    version: str


class SchemaSummary(BaseModel):
    """One entry returned by `/schemas`."""

    name: str
    version: str
    description: str
    field_count: int
    required_fields: list[str]


class SchemasResponse(BaseModel):
    """`/schemas` response body."""

    schemas: list[SchemaSummary]


class ExtractRequestBody(BaseModel):
    """Public-facing wrapper used to build OpenAPI; mirrors `ExtractionRequest`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_name: str = Field(min_length=1, examples=["invoice", "irs_w9"])
    schema_version: str | None = Field(
        default=None,
        description=(
            "Optional pin to a specific schema version. If supplied, the request "
            "fails with 404 when the registry's current version for `schema_name` "
            "differs. Omit to track HEAD."
        ),
        examples=["1.0.0"],
    )
    document_text: str = Field(
        min_length=1,
        # 200,000-character upper bound. Generous (~50K tokens for
        # English) and well within Claude Sonnet 4.6's 200K-token context
        # window, but small enough to (a) reject obvious DoS payloads at
        # the API layer rather than burning a Bedrock call to fail at the
        # context limit, and (b) bound memory cost on the threadpool
        # worker that runs each extraction. The same cap is applied to
        # PDF-extracted text in `extraction.document._process_pdf`. See
        # ADR-0011 for the threat-model rationale.
        max_length=200_000,
    )
    document_id: str | None = None


class ErrorResponse(BaseModel):
    """JSON error body returned for any 4xx/5xx the service controls."""

    detail: str
    correlation_id: str | None = None
