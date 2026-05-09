"""Schema registry.

Schemas can be looked up by name. Built-in schemas register on import.
External callers can register additional schemas via `register_schema`.
"""

from __future__ import annotations

from bedrock_strands_agent.extraction.models import FormSchema

SCHEMA_REGISTRY: dict[str, FormSchema] = {}


def register_schema(schema: FormSchema) -> None:
    """Register `schema` in the global registry, replacing any prior entry."""
    SCHEMA_REGISTRY[schema.name] = schema


def get_schema(name: str) -> FormSchema:
    """Look up a schema by name.

    Raises:
        KeyError: if no schema is registered under `name`. The exception
            message includes the list of known schemas to help callers.
    """
    try:
        return SCHEMA_REGISTRY[name]
    except KeyError as exc:
        known = ", ".join(sorted(SCHEMA_REGISTRY)) or "(none)"
        raise KeyError(f"Unknown schema {name!r}. Registered: {known}") from exc


def resolve_schema(name: str, version: str | None) -> FormSchema:
    """Look up a registered schema, optionally pinned to a specific version.

    Pin-or-fail: if ``version`` is supplied and does not match the registered
    schema's ``version``, raises ``KeyError`` (which the FastAPI routes
    translate to 404). Omit ``version`` to track HEAD of the registry.
    """
    schema = get_schema(name)
    if version is not None and schema.version != version:
        msg = (
            f"Schema {name!r} version {version!r} not registered "
            f"(current registered version: {schema.version!r})"
        )
        raise KeyError(msg)
    return schema


def list_schema_names() -> list[str]:
    """Return the registered schema names, sorted."""
    return sorted(SCHEMA_REGISTRY)


# Register built-ins on import (this is the documented entry point).
from bedrock_strands_agent.extraction.schemas import examples  # noqa: E402, F401

__all__ = [
    "SCHEMA_REGISTRY",
    "get_schema",
    "list_schema_names",
    "register_schema",
    "resolve_schema",
]
