"""Semantic validators for extracted field values.

Single source of truth for SSN/EIN/email/date validation. The agent-side
``@tool``-decorated functions in :mod:`bedrock_strands_agent.agent.tools`
are thin shims that delegate here. The service-side
``ExtractionService._coerce_fields`` (Phase C2) calls :func:`validate_value`
to flag values that match a schema's regex pattern but fail semantic checks
(e.g. ``"00-0000000"`` matches the EIN pattern but isn't a valid EIN once
the registration office vets it; ``"04/15/2026"`` matches a date pattern but
should be rewritten to ISO-8601 by ``normalize_date``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from bedrock_strands_agent.extraction.models import FieldType

SSN_PATTERN: Final = re.compile(r"^\d{3}-\d{2}-\d{4}$")
EIN_PATTERN: Final = re.compile(r"^\d{2}-\d{7}$")
EMAIL_PATTERN: Final = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
DATE_FORMATS: Final = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")


@dataclass(frozen=True)
class ValidatorResult:
    """Outcome of running a semantic validator on a value.

    Attributes:
        ok: ``True`` if the value is acceptable for emission.
        normalized: The value to emit. May differ from input (e.g. dates
            rewritten to ISO-8601 by :func:`normalize_date`).
        reason: Human-readable explanation when ``ok`` is ``False``.
    """

    ok: bool
    normalized: str | int | float | bool | None
    reason: str = ""


def is_ssn(value: str) -> bool:
    r"""Return ``True`` if ``value`` matches ``\d{3}-\d{2}-\d{4}``."""
    return bool(SSN_PATTERN.match(value))


def is_ein(value: str) -> bool:
    r"""Return ``True`` if ``value`` matches ``\d{2}-\d{7}``."""
    return bool(EIN_PATTERN.match(value))


def is_email(value: str) -> bool:
    """Return ``True`` if ``value`` matches a permissive email regex."""
    return bool(EMAIL_PATTERN.match(value))


def normalize_date(value: str) -> str:
    """Parse a date in any of :data:`DATE_FORMATS` and return ISO-8601.

    Raises ``ValueError`` if no format matches.
    """
    text = (value or "").strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    msg = f"could not parse {value!r} as a date"
    raise ValueError(msg)


def validate_value(
    field_type: FieldType, value: str | int | float | bool | None
) -> ValidatorResult:
    """Run the semantic validator for ``field_type`` on ``value``.

    For string-typed fields with semantic constraints (SSN, EIN, EMAIL, DATE)
    returns a :class:`ValidatorResult` whose ``normalized`` value may differ
    from input (e.g. :func:`normalize_date` rewrites to ISO-8601). For all
    other types and ``None``/empty values, returns ``ok=True`` with the input
    as ``normalized``.
    """
    if value in (None, ""):
        return ValidatorResult(ok=True, normalized=value)
    if not isinstance(value, str):
        return ValidatorResult(ok=True, normalized=value)
    if field_type is FieldType.SSN:
        if is_ssn(value):
            return ValidatorResult(ok=True, normalized=value)
        return ValidatorResult(ok=False, normalized=value, reason="not a valid SSN")
    if field_type is FieldType.EIN:
        if is_ein(value):
            return ValidatorResult(ok=True, normalized=value)
        return ValidatorResult(ok=False, normalized=value, reason="not a valid EIN")
    if field_type is FieldType.EMAIL:
        if is_email(value):
            return ValidatorResult(ok=True, normalized=value)
        return ValidatorResult(ok=False, normalized=value, reason="not a valid email")
    if field_type is FieldType.DATE:
        try:
            return ValidatorResult(ok=True, normalized=normalize_date(value))
        except ValueError as exc:
            return ValidatorResult(ok=False, normalized=value, reason=str(exc))
    return ValidatorResult(ok=True, normalized=value)
