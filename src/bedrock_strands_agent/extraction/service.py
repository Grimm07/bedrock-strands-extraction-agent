"""High-level extraction orchestrator.

`ExtractionService.extract` renders the prompt, invokes the agent, parses the
JSON response, validates it against the schema, and (once) re-prompts the
model with the validation errors before giving up.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from bedrock_strands_agent.extraction.models import (
    ExtractedField,
    ExtractionResult,
    FormSchema,
)
from bedrock_strands_agent.extraction.schemas import get_schema

if TYPE_CHECKING:
    from bedrock_strands_agent.agent.builder import AgentBundle
    from bedrock_strands_agent.config import Settings

LOGGER = logging.getLogger(__name__)
_TRACER = trace.get_tracer("bedrock_strands_agent.extraction")
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ExtractionError(RuntimeError):
    """Raised when the model output cannot be turned into a result."""


class ExtractionService:
    """Glue layer between the HTTP API and the Strands agent."""

    def __init__(self, bundle: AgentBundle, *, max_retries: int = 1) -> None:
        """Create a service from a pre-built agent bundle.

        Args:
            bundle: The result of `build_agent(settings)`.
            max_retries: How many times to re-prompt the model with the
                validation errors after the first failed attempt. Defaults
                to 1; set to 0 to disable.
        """
        self._bundle = bundle
        self._max_retries = max(0, max_retries)

    @classmethod
    def from_settings(cls, settings: Settings) -> ExtractionService:
        """Build a service (and the underlying agent) from settings."""
        from bedrock_strands_agent.agent.builder import build_agent

        return cls(build_agent(settings))

    # ------------------------------------------------------------------ #
    def extract(
        self,
        *,
        document_text: str,
        schema_name: str,
        document_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        """Run an extraction and return a fully-validated result."""
        schema = get_schema(schema_name)
        settings = self._bundle.settings
        started = time.perf_counter()

        with _TRACER.start_as_current_span("extraction.run") as span:
            span.set_attribute("extraction.schema", schema.name)
            span.set_attribute("extraction.schema_version", schema.version)
            span.set_attribute("bedrock.model_id", settings.bedrock_model_id)
            if document_id is not None:
                span.set_attribute("extraction.document_id", document_id)
            if correlation_id is not None:
                span.set_attribute("extraction.correlation_id", correlation_id)

            prompt = self._bundle.prompt_renderer.extract(
                schema=schema, document_text=document_text
            )

            previous_errors: list[str] = []
            for attempt in range(self._max_retries + 1):
                if attempt == 0:
                    raw = self._invoke(prompt)
                else:
                    span.set_attribute("extraction.retry_attempt", attempt)
                    LOGGER.warning(
                        "Re-prompting model after validation errors: %s", previous_errors
                    )
                    retry_prompt = self._bundle.prompt_renderer.retry(
                        schema=schema,
                        document_text=document_text,
                        errors=previous_errors,
                    )
                    raw = self._invoke(retry_prompt)

                try:
                    payload = self._parse_json(raw)
                except ExtractionError as exc:
                    previous_errors = [f"Could not parse JSON from response: {exc}"]
                    continue

                fields, warnings = self._coerce_fields(payload, schema)
                fatal = [w for w in warnings if w.startswith("MISSING_REQUIRED:")]
                if not fatal or attempt == self._max_retries:
                    overall = sum(f.confidence for f in fields) / len(fields) if fields else 0.0
                    elapsed_ms = int((time.perf_counter() - started) * 1000)
                    span.set_attribute("extraction.latency_ms", elapsed_ms)
                    span.set_attribute("extraction.field_count", len(fields))
                    span.set_attribute("extraction.warning_count", len(warnings))
                    return ExtractionResult.model_validate(
                        {
                            "schema": schema.name,
                            "schema_version": schema.version,
                            "model_id": settings.bedrock_model_id,
                            "fields": fields,
                            "overall_confidence": round(overall, 4),
                            "warnings": warnings,
                            "latency_ms": elapsed_ms,
                            "extracted_at": datetime.now(UTC),
                            "correlation_id": correlation_id,
                        }
                    )
                previous_errors = fatal

            # Defensive fallback (the loop always returns above on the
            # final attempt). Surfaces as 502 in the API.
            raise ExtractionError("Extraction failed after retries: " + "; ".join(previous_errors))

    # ------------------------------------------------------------------ #
    def _invoke(self, prompt: str) -> str:
        """Call the agent and coerce the response to a plain string."""
        result = self._bundle.agent(prompt)
        if isinstance(result, str):
            return result
        text = getattr(result, "text", None)
        if isinstance(text, str) and text:
            return text
        return str(result)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Strip code fences, then JSON-parse or extract the first `{…}` block."""
        cleaned = _FENCE_RE.sub("", raw).strip()
        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            match = _JSON_BLOCK_RE.search(cleaned)
            if match is None:
                raise ExtractionError("model output did not contain a JSON object") from None
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise ExtractionError(f"could not parse JSON block: {exc}") from exc
        if not isinstance(payload, dict):
            raise ExtractionError("expected a JSON object at the top level")
        return payload

    @staticmethod
    def _coerce_fields(
        payload: dict[str, Any], schema: FormSchema
    ) -> tuple[list[ExtractedField], list[str]]:
        """Project `payload['fields']` onto the schema, returning warnings."""
        warnings: list[str] = []
        fields_node = payload.get("fields")
        if not isinstance(fields_node, list):
            raise ExtractionError("response is missing a 'fields' array")

        by_name: dict[str, dict[str, Any]] = {}
        for item in fields_node:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                by_name[item["name"]] = item

        out: list[ExtractedField] = []
        for spec in schema.fields:
            raw = by_name.get(spec.name)
            if raw is None:
                if spec.required:
                    warnings.append(f"MISSING_REQUIRED:{spec.name}")
                else:
                    warnings.append(f"missing optional field: {spec.name}")
                out.append(ExtractedField(name=spec.name, value=None, confidence=0.0))
                continue

            value = raw.get("value")
            confidence = float(raw.get("confidence", 0.0) or 0.0)
            confidence = max(0.0, min(1.0, confidence))
            excerpt = raw.get("source_excerpt")

            if (
                spec.pattern
                and isinstance(value, str)
                and value
                and not re.match(spec.pattern, value)
            ):
                warnings.append(
                    f"pattern mismatch for {spec.name!r}: "
                    f"value {value!r} did not match {spec.pattern!r}"
                )

            if spec.required and (value is None or value == ""):
                warnings.append(f"MISSING_REQUIRED:{spec.name}")

            out.append(
                ExtractedField(
                    name=spec.name,
                    value=value,
                    confidence=confidence,
                    source_excerpt=excerpt if isinstance(excerpt, str) else None,
                )
            )
        return out, warnings
