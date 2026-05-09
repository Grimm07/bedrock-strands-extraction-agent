"""Weighted overall-confidence calibration (Phase C5).

Replaces the v0.1 simple-average overall confidence with a formula that
weights required fields, validator-passed values, and citation-verified
values. See ADR-0004 for the rationale.

Pure functions: take field list + schema, return ``[0, 1]``-bounded floats.
``ExtractionService.extract`` (Phase C2) replaces its current ``sum(...)/len(...)``
line with :func:`compute_overall`; per-field weighting flows through
:func:`compute_field_confidence` once Phase C2 wires citation/validator
context into the loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bedrock_strands_agent.extraction.models import (
        ExtractedField,
        FieldDefinition,
        FormSchema,
    )

_REQUIRED_WEIGHT = 2.0
_OPTIONAL_WEIGHT = 1.0
_VALIDATOR_BONUS = 1.2
_CITATION_BONUS = 1.1
# Divisor that keeps the all-bonuses-true case bounded by 1.0.
_FIELD_CONF_DIVISOR = 2.0


def compute_field_confidence(
    model_self: float,
    *,
    required: bool,
    validator_passed: bool,
    citation_verified: bool,
) -> float:
    """Apply per-field weight bonuses and clamp to ``[0, 1]``."""
    weight = _REQUIRED_WEIGHT if required else _OPTIONAL_WEIGHT
    if validator_passed:
        weight *= _VALIDATOR_BONUS
    if citation_verified:
        weight *= _CITATION_BONUS
    return max(0.0, min(1.0, model_self * weight / _FIELD_CONF_DIVISOR))


def compute_overall(fields: list[ExtractedField], schema: FormSchema) -> float:
    """Return the weighted overall confidence for the extraction.

    Returns ``0.0`` when ``fields`` is empty or any required field is
    missing/empty (a missing required field is a hard failure, not a soft
    signal). Otherwise returns the required/optional-weighted mean of
    per-field confidences, rounded to four decimals.
    """
    if not fields:
        return 0.0
    spec_by_name: dict[str, FieldDefinition] = {f.name: f for f in schema.fields}
    weighted_sum = 0.0
    weights = 0.0
    for f in fields:
        spec = spec_by_name.get(f.name)
        if spec is None:
            # Extra field not in schema — ignore for overall.
            continue
        if spec.required and (f.value is None or f.value == ""):
            return 0.0
        weight = _REQUIRED_WEIGHT if spec.required else _OPTIONAL_WEIGHT
        weighted_sum += f.confidence * weight
        weights += weight
    if weights == 0.0:
        return 0.0
    return round(min(1.0, max(0.0, weighted_sum / weights)), 4)
