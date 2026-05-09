"""Strands tools exposed to the agent.

Each tool is a `@tool`-decorated function with a Google-style docstring; the
Strands runtime introspects the docstring to populate the tool spec. Tests can
call the underlying callable via `getattr(tool, "func", tool)`.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from strands import tool

from bedrock_strands_agent.extraction.schemas import SCHEMA_REGISTRY, get_schema

_SSN_RE = re.compile(r"^\d{3}-\d{2}-\d{4}$")
_EIN_RE = re.compile(r"^\d{2}-\d{7}$")
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
_DATE_FORMATS = ("%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%b %d, %Y", "%d %B %Y")


@tool
def list_schemas() -> list[str]:
    """List the schema names available to the extraction service.

    Returns:
        A sorted list of registered schema names.
    """
    return sorted(SCHEMA_REGISTRY)


@tool
def describe_schema(name: str) -> dict[str, object]:
    """Return a structured description of one registered schema.

    Args:
        name: The schema name (case-sensitive).

    Returns:
        A dict with `name`, `version`, `description`, and a list of `fields`.

    Raises:
        ValueError: if the schema is not registered.
    """
    try:
        schema = get_schema(name)
    except KeyError as exc:
        raise ValueError(str(exc)) from exc
    return {
        "name": schema.name,
        "version": schema.version,
        "description": schema.description,
        "fields": [
            {
                "name": f.name,
                "type": f.type.value,
                "required": f.required,
                "description": f.description,
                "pattern": f.pattern,
            }
            for f in schema.fields
        ],
    }


@tool
def validate_ssn(value: str) -> bool:
    """Return True if `value` is a US SSN formatted as 999-99-9999.

    Args:
        value: A candidate SSN string.

    Returns:
        True if `value` matches the expected pattern, False otherwise.
    """
    return bool(_SSN_RE.match(value or ""))


@tool
def validate_ein(value: str) -> bool:
    """Return True if `value` is a US EIN formatted as 99-9999999.

    Args:
        value: A candidate EIN string.

    Returns:
        True if `value` matches the expected pattern, False otherwise.
    """
    return bool(_EIN_RE.match(value or ""))


@tool
def validate_email(value: str) -> bool:
    """Return True if `value` looks like a valid email address.

    Args:
        value: A candidate email string.

    Returns:
        True if `value` matches a permissive email regex, False otherwise.
    """
    return bool(_EMAIL_RE.match(value or ""))


@tool
def normalize_date(value: str) -> str:
    """Normalize a date string to ISO-8601 (YYYY-MM-DD).

    Accepts ISO-8601, US `MM/DD/YYYY`, and long-form
    `Month D, YYYY` / `D Month YYYY` representations.

    Args:
        value: A date string.

    Returns:
        The date in ISO-8601 (YYYY-MM-DD) form.

    Raises:
        ValueError: if `value` is not in a recognised format.
    """
    text = (value or "").strip()
    for fmt in _DATE_FORMATS:
        try:
            parsed = datetime.strptime(text, fmt).date()
        except ValueError:
            continue
        return parsed.isoformat()
    raise ValueError(f"could not parse {value!r} as a date")


@tool
def today_iso() -> str:
    """Return today's date (UTC) as an ISO-8601 string.

    Returns:
        Today's date in YYYY-MM-DD form.
    """
    return date.fromtimestamp(datetime.now(UTC).timestamp()).isoformat()


DEFAULT_TOOLS: list[object] = [
    list_schemas,
    describe_schema,
    validate_ssn,
    validate_ein,
    validate_email,
    normalize_date,
    today_iso,
]
