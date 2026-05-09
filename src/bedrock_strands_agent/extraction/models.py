"""Pydantic models for form schemas, requests, and results."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FieldType(StrEnum):
    """Supported field value types in a `FormSchema`."""

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    EMAIL = "email"
    SSN = "ssn"
    EIN = "ein"
    CURRENCY = "currency"


class FieldDefinition(BaseModel):
    """A single field a schema asks the agent to extract."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    description: str = Field(default="", description="Human-readable hint for the model.")
    type: FieldType = Field(default=FieldType.STRING)
    required: bool = Field(default=False)
    examples: tuple[str, ...] = Field(default=())
    pattern: str | None = Field(default=None, description="Optional regex the value must match.")


class FormSchema(BaseModel):
    """A named, versioned bundle of `FieldDefinition`s."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    name: str = Field(min_length=1)
    version: str = Field(default="1.0.0")
    description: str = Field(default="")
    fields: tuple[FieldDefinition, ...]

    @field_validator("fields")
    @classmethod
    def _non_empty_unique(cls, value: tuple[FieldDefinition, ...]) -> tuple[FieldDefinition, ...]:
        if not value:
            raise ValueError("FormSchema.fields must contain at least one field.")
        names = [f.name for f in value]
        if len(set(names)) != len(names):
            raise ValueError("FormSchema.fields must have unique names.")
        return value


class ExtractedField(BaseModel):
    """A single extracted value, plus model self-assessed confidence."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1)
    value: str | int | float | bool | None = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    source_excerpt: str | None = Field(
        default=None,
        description="Short verbatim excerpt of the document that supports the value.",
    )


class ExtractionRequest(BaseModel):
    """HTTP request body for `/extract`."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    schema_name: str = Field(min_length=1, description="Name of a registered FormSchema.")
    document_text: str = Field(min_length=1, description="The text of the document to extract.")
    document_id: str | None = Field(default=None, description="Optional caller-side identifier.")


class ExtractionResult(BaseModel):
    """Service-level extraction result returned by `ExtractionService.extract`."""

    model_config = ConfigDict(str_strip_whitespace=True)

    schema_: str = Field(alias="schema")
    schema_version: str
    model_id: str
    fields: list[ExtractedField]
    overall_confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)
    latency_ms: int = Field(ge=0)
    extracted_at: datetime
    correlation_id: str | None = None
