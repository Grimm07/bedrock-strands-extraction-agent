"""Tests for :mod:`bedrock_strands_agent.extraction.validators`."""

from __future__ import annotations

import pytest

from bedrock_strands_agent.extraction.models import FieldType
from bedrock_strands_agent.extraction.validators import (
    ValidatorResult,
    is_ein,
    is_email,
    is_ssn,
    normalize_date,
    validate_value,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [("123-45-6789", True), ("000-00-0000", True), ("1234-56-789", False), ("", False)],
)
def test_is_ssn(value: str, expected: bool) -> None:
    assert is_ssn(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12-3456789", True), ("1-23456789", False), ("12-345678", False), ("", False)],
)
def test_is_ein(value: str, expected: bool) -> None:
    assert is_ein(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a@b.co", True),
        ("first.last+filter@example.com", True),
        ("missing-at.example.com", False),
        ("", False),
    ],
)
def test_is_email(value: str, expected: bool) -> None:
    assert is_email(value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-04-15", "2026-04-15"),
        ("04/15/2026", "2026-04-15"),
        ("April 15, 2026", "2026-04-15"),
        ("Apr 15, 2026", "2026-04-15"),
        ("15 April 2026", "2026-04-15"),
    ],
)
def test_normalize_date_round_trips(value: str, expected: str) -> None:
    assert normalize_date(value) == expected


def test_normalize_date_unparseable_raises() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        normalize_date("not a date")


# --------------------------------------------------------------------------- #
# validate_value
# --------------------------------------------------------------------------- #
def test_validate_value_passes_none() -> None:
    out = validate_value(FieldType.SSN, None)
    assert out == ValidatorResult(ok=True, normalized=None)


def test_validate_value_passes_empty_string() -> None:
    out = validate_value(FieldType.SSN, "")
    assert out == ValidatorResult(ok=True, normalized="")


def test_validate_value_string_field_passes_through() -> None:
    out = validate_value(FieldType.STRING, "anything")
    assert out == ValidatorResult(ok=True, normalized="anything")


def test_validate_value_ssn_ok() -> None:
    out = validate_value(FieldType.SSN, "123-45-6789")
    assert out.ok
    assert out.normalized == "123-45-6789"


def test_validate_value_ssn_bad() -> None:
    out = validate_value(FieldType.SSN, "1234-56-789")
    assert not out.ok
    assert "SSN" in out.reason


def test_validate_value_ein_bad() -> None:
    out = validate_value(FieldType.EIN, "1-23456789")
    assert not out.ok
    assert "EIN" in out.reason


def test_validate_value_email_bad() -> None:
    out = validate_value(FieldType.EMAIL, "missing-at.example.com")
    assert not out.ok


def test_validate_value_date_normalizes() -> None:
    out = validate_value(FieldType.DATE, "04/15/2026")
    assert out.ok
    assert out.normalized == "2026-04-15"


def test_validate_value_date_bad() -> None:
    out = validate_value(FieldType.DATE, "not a date")
    assert not out.ok
    assert "could not parse" in out.reason


def test_validate_value_non_string_value_passes_through() -> None:
    out = validate_value(FieldType.INTEGER, 42)
    assert out == ValidatorResult(ok=True, normalized=42)
