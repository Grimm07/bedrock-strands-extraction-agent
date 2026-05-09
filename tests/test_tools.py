from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from bedrock_strands_agent.agent import tools as tool_mod


def _call(tool: Any, *args: Any, **kwargs: Any) -> Any:
    """Invoke the underlying function from a Strands @tool decorator."""
    fn = getattr(tool, "func", None) or getattr(tool, "__wrapped__", None) or tool
    return fn(*args, **kwargs)


def test_list_schemas_includes_builtins() -> None:
    names = _call(tool_mod.list_schemas)
    assert "irs_w9" in names
    assert "invoice" in names


def test_describe_schema_known() -> None:
    out = _call(tool_mod.describe_schema, "invoice")
    assert out["name"] == "invoice"
    assert any(f["name"] == "invoice_number" for f in out["fields"])


def test_describe_schema_unknown_raises() -> None:
    with pytest.raises(ValueError, match="Unknown schema"):
        _call(tool_mod.describe_schema, "nope")


@pytest.mark.parametrize(
    ("value", "expected"),
    [("123-45-6789", True), ("000-00-0000", True), ("1234-56-789", False), ("", False)],
)
def test_validate_ssn(value: str, expected: bool) -> None:
    assert _call(tool_mod.validate_ssn, value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [("12-3456789", True), ("1-23456789", False), ("12-345678", False), ("", False)],
)
def test_validate_ein(value: str, expected: bool) -> None:
    assert _call(tool_mod.validate_ein, value) is expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("a@b.co", True),
        ("first.last+filter@example.com", True),
        ("missing-at.example.com", False),
        ("", False),
    ],
)
def test_validate_email(value: str, expected: bool) -> None:
    assert _call(tool_mod.validate_email, value) is expected


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
def test_normalize_date(value: str, expected: str) -> None:
    assert _call(tool_mod.normalize_date, value) == expected


def test_normalize_date_unparseable() -> None:
    with pytest.raises(ValueError, match="could not parse"):
        _call(tool_mod.normalize_date, "not a date")


def test_today_iso_round_trips() -> None:
    iso = _call(tool_mod.today_iso)
    assert date.fromisoformat(iso)
