"""High-level extraction orchestrator.

:meth:`ExtractionService.extract` (text path) and
:meth:`ExtractionService.extract_document` (text-or-vision auto-routing) share
the same retry-loop, parser, and post-hoc validation pipeline. Three layers of
post-hoc validation:

1. **Pattern match** — the schema's optional regex.
2. **Semantic validator** — :func:`validate_value` on SSN / EIN / EMAIL /
   DATE fields.
3. **Citation verifier** — :func:`verify_excerpt` against the original
   document. Disabled in vision mode (no canonical text to verify against).

Vision mode bypasses the Strands :class:`Agent` and calls Bedrock Converse
directly via :func:`invoke_multimodal`. See ADR-0008 for the rationale.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from opentelemetry import trace

from bedrock_strands_agent.agent.bedrock_retry import invoke_with_retry
from bedrock_strands_agent.extraction.citations import verify_excerpt
from bedrock_strands_agent.extraction.confidence import (
    compute_field_confidence,
    compute_overall,
)
from bedrock_strands_agent.extraction.document import (
    UnsupportedDocumentError,
    process_upload,
)
from bedrock_strands_agent.extraction.models import (
    ExtractedField,
    ExtractionResult,
    FormSchema,
)
from bedrock_strands_agent.extraction.multimodal import invoke_multimodal
from bedrock_strands_agent.extraction.schemas import resolve_schema
from bedrock_strands_agent.extraction.validators import validate_value

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from typing import Literal

    from bedrock_strands_agent.agent.builder import AgentBundle
    from bedrock_strands_agent.config import Settings
    from bedrock_strands_agent.extraction.document import ImageFormat

LOGGER = logging.getLogger(__name__)
_TRACER = trace.get_tracer("bedrock_strands_agent.extraction")
_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}")
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


class ExtractionError(RuntimeError):
    """Raised when the model output cannot be turned into a result."""


# Re-export so callers can ``from bedrock_strands_agent.extraction import
# UnsupportedDocumentError`` and route HTTP responses without reaching into
# the document module.
__all__ = ["ExtractionError", "ExtractionService", "UnsupportedDocumentError"]


class ExtractionService:
    """Glue layer between the HTTP API and the Strands agent."""

    def __init__(self, bundle: AgentBundle, *, max_retries: int = 2) -> None:
        """Create a service from a pre-built agent bundle.

        Args:
            bundle: The result of ``build_agent(settings)``.
            max_retries: Maximum re-prompts after the first failed attempt
                (default 2 — Phase C2).
        """
        self._bundle = bundle
        self._max_retries = max(0, max_retries)

    @classmethod
    def from_settings(cls, settings: Settings) -> ExtractionService:
        """Build a service (and the underlying agent) from settings."""
        from bedrock_strands_agent.agent.builder import build_agent

        return cls(build_agent(settings))

    # ------------------------------------------------------------------ #
    # Public extraction entrypoints
    # ------------------------------------------------------------------ #
    def extract(
        self,
        *,
        document_text: str,
        schema_name: str,
        schema_version: str | None = None,
        document_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        """Run a text-mode extraction and return a fully-validated result."""
        schema = resolve_schema(schema_name, schema_version)
        initial = self._bundle.prompt_renderer.extract(schema=schema, document_text=document_text)
        return self._run_extraction_loop(
            schema=schema,
            document_text=document_text,
            mode="text",
            initial_prompt=initial,
            invoke_fn=self._invoke,
            extra_span_attrs={},
            document_id=document_id,
            correlation_id=correlation_id,
        )

    async def extract_stream(
        self,
        *,
        document_text: str,
        schema_name: str,
        schema_version: str | None = None,
        document_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream a text-mode extraction as Strands events.

        Yields ``{"type": "chunk", "text": <delta>}`` for each text delta the
        model emits, followed by exactly one terminal event: either
        ``{"type": "result", "result": <ExtractionResult.model_dump>}`` on
        success, or ``{"type": "error", "detail": <str>}`` on a parse or
        validation failure of the accumulated text.

        The retry loop that ``extract`` uses is intentionally NOT applied
        here — re-prompting mid-stream is a poor UX. Callers that need
        self-correction should fall back to ``POST /extract`` after a
        stream-side error. Consequently the ``extraction.stream`` span
        never opens an ``extraction.attempt`` sub-span (see ADR-0009);
        trace queries that filter on ``extraction.attempt`` will not
        match streaming traces by design.

        See ``docs/ROADMAP.md`` "Released since 0.2.0" — partial-field
        streaming is a future enhancement on top of this raw-delta path.
        """
        schema = resolve_schema(schema_name, schema_version)
        prompt = self._bundle.prompt_renderer.extract(schema=schema, document_text=document_text)
        settings = self._bundle.settings
        started = time.perf_counter()
        accumulated: list[str] = []

        with _TRACER.start_as_current_span("extraction.stream") as span:
            span.set_attribute("extraction.schema", schema.name)
            span.set_attribute("extraction.schema_version", schema.version)
            span.set_attribute("bedrock.model_id", settings.bedrock_model_id)
            span.set_attribute("extraction.mode", "stream")
            if document_id is not None:
                span.set_attribute("extraction.document_id", document_id)
            if correlation_id is not None:
                span.set_attribute("extraction.correlation_id", correlation_id)

            async for event in self._bundle.agent.stream_async(prompt):
                if not isinstance(event, dict):
                    continue
                delta = event.get("data")
                if isinstance(delta, str) and delta:
                    accumulated.append(delta)
                    yield {"type": "chunk", "text": delta}

            raw = "".join(accumulated)
            span.set_attribute("extraction.streamed_chars", len(raw))

            try:
                payload = self._parse_json(raw)
            except ExtractionError as exc:
                yield {
                    "type": "error",
                    "detail": f"Could not parse JSON from streamed response: {exc}",
                }
                return

            fields, warnings, _, _ = self._coerce_fields(
                payload, schema, document_text=document_text
            )
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            span.set_attribute("extraction.latency_ms", elapsed_ms)
            span.set_attribute("extraction.field_count", len(fields))
            # Mirrored from the non-streaming `extraction.run` span so
            # dashboards aggregating across modes don't drop streaming traffic.
            span.set_attribute("extraction.warning_count", len(warnings))
            result = ExtractionResult.model_validate(
                {
                    "schema": schema.name,
                    "schema_version": schema.version,
                    "model_id": settings.bedrock_model_id,
                    "fields": fields,
                    "overall_confidence": compute_overall(fields, schema),
                    "warnings": warnings,
                    "latency_ms": elapsed_ms,
                    "extracted_at": datetime.now(UTC),
                    "correlation_id": correlation_id,
                }
            )
            yield {"type": "result", "result": result.model_dump(by_alias=True, mode="json")}

    def extract_document(
        self,
        *,
        upload_bytes: bytes,
        content_type: str,
        schema_name: str,
        schema_version: str | None = None,
        document_id: str | None = None,
        correlation_id: str | None = None,
    ) -> ExtractionResult:
        """Auto-route an uploaded document to the text or vision path.

        See ``docs/extraction-modes.md`` and ADR-0008.
        """
        doc = process_upload(upload_bytes, content_type)
        if doc.mode == "text":
            return self.extract(
                document_text=doc.text or "",
                schema_name=schema_name,
                schema_version=schema_version,
                document_id=document_id,
                correlation_id=correlation_id,
            )
        if doc.image_format is None:  # pragma: no cover — invariant from process_upload
            msg = "image upload missing image_format"
            raise ExtractionError(msg)
        return self._extract_via_vision(
            images=doc.images,
            image_format=doc.image_format,
            schema_name=schema_name,
            schema_version=schema_version,
            document_id=document_id,
            correlation_id=correlation_id,
        )

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _extract_via_vision(
        self,
        *,
        images: tuple[bytes, ...],
        image_format: ImageFormat,
        schema_name: str,
        schema_version: str | None,
        document_id: str | None,
        correlation_id: str | None,
    ) -> ExtractionResult:
        schema = resolve_schema(schema_name, schema_version)
        settings = self._bundle.settings
        initial = self._bundle.prompt_renderer.extract_image(schema=schema)

        def vision_invoke(prompt: str) -> str:
            return invoke_multimodal(
                region=settings.aws_region,
                model_id=settings.bedrock_model_id,
                prompt=prompt,
                images=images,
                image_format=image_format,
                max_tokens=settings.bedrock_max_tokens,
                temperature=settings.bedrock_temperature,
                top_p=settings.bedrock_top_p,
            )

        return self._run_extraction_loop(
            schema=schema,
            document_text="",  # vision mode disables citation verification
            mode="vision",
            initial_prompt=initial,
            invoke_fn=vision_invoke,
            extra_span_attrs={"extraction.image_count": len(images)},
            document_id=document_id,
            correlation_id=correlation_id,
        )

    def _run_extraction_loop(
        self,
        *,
        schema: FormSchema,
        document_text: str,
        mode: Literal["text", "vision"],
        initial_prompt: str,
        invoke_fn: Callable[[str], str],
        extra_span_attrs: dict[str, Any],
        document_id: str | None,
        correlation_id: str | None,
    ) -> ExtractionResult:
        settings = self._bundle.settings
        started = time.perf_counter()

        with _TRACER.start_as_current_span("extraction.run") as span:
            span.set_attribute("extraction.schema", schema.name)
            span.set_attribute("extraction.schema_version", schema.version)
            span.set_attribute("bedrock.model_id", settings.bedrock_model_id)
            span.set_attribute("extraction.mode", mode)
            for k, v in extra_span_attrs.items():
                span.set_attribute(k, v)
            if document_id is not None:
                span.set_attribute("extraction.document_id", document_id)
            if correlation_id is not None:
                span.set_attribute("extraction.correlation_id", correlation_id)

            previous_validation_errors: list[str] = []
            previous_citation_failures: list[dict[str, Any]] = []
            previous_validator_failures: list[dict[str, Any]] = []

            for attempt in range(self._max_retries + 1):
                with _TRACER.start_as_current_span(
                    "extraction.attempt", attributes={"attempt": attempt}
                ) as sub_span:
                    if attempt == 0:
                        raw = invoke_fn(initial_prompt)
                    else:
                        span.set_attribute("extraction.retry_attempt", attempt)
                        LOGGER.warning(
                            "Re-prompting after validation issues "
                            "(mode=%s): errors=%d citation_fails=%d validator_fails=%d",
                            mode,
                            len(previous_validation_errors),
                            len(previous_citation_failures),
                            len(previous_validator_failures),
                        )
                        retry_prompt = self._bundle.prompt_renderer.retry(
                            schema=schema,
                            document_text=document_text,
                            validation_errors=previous_validation_errors,
                            citation_failures=previous_citation_failures,
                            validator_failures=previous_validator_failures,
                        )
                        raw = invoke_fn(retry_prompt)

                    try:
                        payload = self._parse_json(raw)
                    except ExtractionError as exc:
                        previous_validation_errors = [f"Could not parse JSON from response: {exc}"]
                        previous_citation_failures = []
                        previous_validator_failures = []
                        sub_span.add_event("attempt.failed", {"reason": "json-parse-failed"})
                        continue

                    fields, warnings, citation_failures, validator_failures = self._coerce_fields(
                        payload, schema, document_text=document_text
                    )
                    sub_span.set_attribute("attempt.warnings", len(warnings))
                    sub_span.set_attribute("attempt.citation_failures", len(citation_failures))
                    sub_span.set_attribute("attempt.validator_failures", len(validator_failures))

                    fatal = [w for w in warnings if w.startswith("MISSING_REQUIRED:")]
                    is_last_attempt = attempt == self._max_retries
                    if not fatal or is_last_attempt:
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
                                "overall_confidence": compute_overall(fields, schema),
                                "warnings": warnings,
                                "latency_ms": elapsed_ms,
                                "extracted_at": datetime.now(UTC),
                                "correlation_id": correlation_id,
                            }
                        )

                    previous_validation_errors = fatal
                    previous_citation_failures = citation_failures
                    previous_validator_failures = validator_failures

            raise ExtractionError(
                "Extraction failed after retries: " + "; ".join(previous_validation_errors)
            )

    def _invoke(self, prompt: str) -> str:
        """Call the Strands agent (text mode) with retry; coerce to string."""
        result = invoke_with_retry(self._bundle.agent, prompt)
        if isinstance(result, str):
            return result
        text = getattr(result, "text", None)
        if isinstance(text, str) and text:
            return text
        return str(result)

    @staticmethod
    def _parse_json(raw: str) -> dict[str, Any]:
        """Strip code fences, then JSON-parse or extract the first ``{…}`` block."""
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
        payload: dict[str, Any],
        schema: FormSchema,
        *,
        document_text: str,
    ) -> tuple[
        list[ExtractedField],
        list[str],
        list[dict[str, Any]],
        list[dict[str, Any]],
    ]:
        """Project ``payload['fields']`` onto the schema, returning four lists.

        Citation verification is skipped when ``document_text`` is empty
        (vision mode); ``citation_failures`` will always be empty in that
        case and per-field ``citation_verified`` stays ``False``.
        """
        warnings: list[str] = []
        citation_failures: list[dict[str, Any]] = []
        validator_failures: list[dict[str, Any]] = []

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
            model_self = float(raw.get("confidence", 0.0) or 0.0)
            model_self = max(0.0, min(1.0, model_self))
            raw_excerpt = raw.get("source_excerpt")
            excerpt = raw_excerpt if isinstance(raw_excerpt, str) else None

            pattern_ok = True
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
                pattern_ok = False

            if spec.required and (value is None or value == ""):
                warnings.append(f"MISSING_REQUIRED:{spec.name}")

            validator_passed = True
            if pattern_ok and value not in (None, ""):
                vr = validate_value(spec.type, value)
                if not vr.ok:
                    validator_failures.append(
                        {"name": spec.name, "value": value, "reason": vr.reason}
                    )
                    validator_passed = False
                elif vr.normalized != value:
                    value = vr.normalized

            citation_verified = False
            if excerpt is not None and document_text:
                cc = verify_excerpt(document_text, excerpt)
                citation_verified = cc.verbatim
                if not cc.verbatim and value not in (None, ""):
                    citation_failures.append(
                        {
                            "name": spec.name,
                            "excerpt": excerpt,
                            "overlap_pct": cc.overlap_pct,
                        }
                    )

            calibrated = compute_field_confidence(
                model_self,
                required=spec.required,
                validator_passed=validator_passed,
                citation_verified=citation_verified,
            )

            out.append(
                ExtractedField(
                    name=spec.name,
                    value=value,
                    confidence=calibrated,
                    source_excerpt=excerpt,
                )
            )
        return out, warnings, citation_failures, validator_failures
