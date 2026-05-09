from __future__ import annotations

import pytest
from pydantic import ValidationError

from bedrock_strands_agent.extraction.models import (
    ExtractedField,
    ExtractionRequest,
    FieldDefinition,
    FieldType,
    FormSchema,
)
from bedrock_strands_agent.extraction.schemas import get_schema


def test_extraction_request_forbids_extra_keys() -> None:
    with pytest.raises(ValidationError):
        ExtractionRequest.model_validate(
            {"schema_name": "invoice", "document_text": "x", "rogue": True}
        )


def test_form_schema_rejects_empty_fields() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        FormSchema(name="x", fields=())


def test_form_schema_rejects_duplicate_names() -> None:
    with pytest.raises(ValidationError, match="unique"):
        FormSchema(
            name="x",
            fields=(
                FieldDefinition(name="a"),
                FieldDefinition(name="a"),
            ),
        )


def test_unknown_schema_lookup_lists_known() -> None:
    with pytest.raises(KeyError) as exc_info:
        get_schema("does-not-exist")
    msg = str(exc_info.value)
    assert "irs_w9" in msg
    assert "invoice" in msg


def test_extracted_field_clamped_confidence() -> None:
    with pytest.raises(ValidationError):
        ExtractedField(name="x", confidence=2.5)


def test_field_type_is_strenum() -> None:
    assert FieldType.STRING.value == "string"
    assert FieldType("string") is FieldType.STRING
