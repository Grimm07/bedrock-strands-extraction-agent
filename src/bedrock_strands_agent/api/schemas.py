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
    document_text: str = Field(min_length=1)
    document_id: str | None = None


class ErrorResponse(BaseModel):
    """JSON error body returned for any 4xx/5xx the service controls."""

    detail: str
    correlation_id: str | None = None
