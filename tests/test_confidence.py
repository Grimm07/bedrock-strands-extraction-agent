"""Tests for :mod:`bedrock_strands_agent.extraction.confidence`."""

from __future__ import annotations

import pytest

from bedrock_strands_agent.extraction.confidence import (
    compute_field_confidence,
    compute_overall,
)
from bedrock_strands_agent.extraction.models import (
    ExtractedField,
    FieldDefinition,
    FieldType,
    FormSchema,
)


def _schema(*fields: FieldDefinition) -> FormSchema:
    return FormSchema(name="test", version="1.0.0", fields=fields)


# --------------------------------------------------------------------------- #
# compute_field_confidence
# --------------------------------------------------------------------------- #
def test_per_field_optional_no_bonuses_is_half() -> None:
    out = compute_field_confidence(
        1.0, required=False, validator_passed=False, citation_verified=False
    )
    assert out == pytest.approx(0.5, abs=1e-3)


def test_per_field_required_no_bonuses_is_one() -> None:
    out = compute_field_confidence(
        1.0, required=True, validator_passed=False, citation_verified=False
    )
    assert out == 1.0


def test_per_field_all_bonuses_clamps_to_one() -> None:
    out = compute_field_confidence(
        1.0, required=True, validator_passed=True, citation_verified=True
    )
    assert out == 1.0


def test_per_field_optional_with_bonuses() -> None:
    """Optional + validator + citation = 1.0 * 1.2 * 1.1 / 2 = 0.66."""
    out = compute_field_confidence(
        1.0, required=False, validator_passed=True, citation_verified=True
    )
    assert out == pytest.approx(0.66, abs=1e-3)


def test_per_field_zero_self_stays_zero() -> None:
    out = compute_field_confidence(
        0.0, required=True, validator_passed=True, citation_verified=True
    )
    assert out == 0.0


# --------------------------------------------------------------------------- #
# compute_overall
# --------------------------------------------------------------------------- #
def test_overall_weighted_mean() -> None:
    schema = _schema(
        FieldDefinition(name="a", type=FieldType.STRING, required=True),
        FieldDefinition(name="b", type=FieldType.STRING, required=False),
    )
    fields = [
        ExtractedField(name="a", value="x", confidence=1.0),
        ExtractedField(name="b", value="y", confidence=0.5),
    ]
    # Required*2 → 1.0 * 2 = 2.0; Optional*1 → 0.5 * 1 = 0.5; total weights 3
    # weighted_sum 2.5 → 2.5/3 = 0.8333…
    assert compute_overall(fields, schema) == pytest.approx(0.8333, abs=1e-3)


def test_overall_zero_when_required_missing() -> None:
    schema = _schema(
        FieldDefinition(name="a", type=FieldType.STRING, required=True),
        FieldDefinition(name="b", type=FieldType.STRING, required=False),
    )
    fields = [
        ExtractedField(name="a", value=None, confidence=0.0),
        ExtractedField(name="b", value="y", confidence=1.0),
    ]
    assert compute_overall(fields, schema) == 0.0


def test_overall_zero_when_required_empty_string() -> None:
    schema = _schema(FieldDefinition(name="a", type=FieldType.STRING, required=True))
    fields = [ExtractedField(name="a", value="", confidence=0.5)]
    assert compute_overall(fields, schema) == 0.0


def test_overall_with_no_fields_is_zero() -> None:
    schema = _schema(FieldDefinition(name="a", type=FieldType.STRING))
    assert compute_overall([], schema) == 0.0


def test_overall_ignores_extra_fields_not_in_schema() -> None:
    schema = _schema(
        FieldDefinition(name="a", type=FieldType.STRING, required=False),
    )
    fields = [
        ExtractedField(name="a", value="x", confidence=1.0),
        ExtractedField(name="z", value="extra", confidence=0.0),  # not in schema
    ]
    assert compute_overall(fields, schema) == 1.0


def test_overall_clamps_at_one() -> None:
    schema = _schema(FieldDefinition(name="a", type=FieldType.STRING, required=True))
    fields = [ExtractedField(name="a", value="x", confidence=1.0)]
    assert compute_overall(fields, schema) == 1.0
