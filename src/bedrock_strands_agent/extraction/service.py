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

from botocore.exceptions import BotoCoreError, ClientError
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, StrictBool, ValidationError

from bedrock_strands_agent.agent.bedrock_retry import invoke_with_retry
from bedrock_strands_agent.extraction.citations import value_anchored_in_excerpt, verify_excerpt
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
    FieldType,
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

# Field types whose validators legitimately rewrite the model's emitted value
# into a canonical form that won't substring-match the document surface form
# (e.g. ``"January 15, 2026"`` in the doc -> ``value="2026-01-15"``). The
# value-anchored citation check (ADR-0011) is skipped for these because it
# would false-positive on the happy path. Non-string types are already skipped
# inside ``value_anchored_in_excerpt`` itself; this set is only for the
# string-returning normalising types.
_NORMALISING_STRING_TYPES: frozenset[FieldType] = frozenset({FieldType.DATE})


class _GroundingItem(BaseModel):
    """Schema-enforced shape of a single grounding-verifier entry.

    ``extra='forbid'`` rejects any unexpected keys (a model that returns
    ``{"name":..., "present":..., "explanation":"..."}`` fails validation
    rather than silently dropping the extra). ``StrictBool`` rejects
    coerced values like ``"yes"`` / ``1`` / ``"on"``: these would have
    been treated as ``True`` (missing-failure!) under default Pydantic
    coercion. Strict mode here is deliberate — the verifier sits at a
    security-relevant boundary where loose typing favours an attacker.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    present: StrictBool


class _GroundingResponse(BaseModel):
    """The full grounding-verifier response: a list of per-field decisions.

    Pydantic validation (with ``extra='forbid'`` here AND on
    ``_GroundingItem``) replaces the previous hand-rolled
    ``isinstance(entry, dict) and isinstance(entry.get('name'), str)``
    walker. Behavioural change worth calling out: any deviation —
    missing key, wrong type, extra key — fails the WHOLE response and
    falls back to "no failures" (fail open). Previously the walker
    silently skipped malformed entries while keeping the well-formed
    ones. The new strict-and-fail-open contract is safer at the
    threat-model boundary; the eventual migration to
    ``BedrockModel.structured_output`` would force the shape at the
    model layer instead of post-parse.
    """

    model_config = ConfigDict(extra="forbid")

    groundings: list[_GroundingItem]


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

            fields, warnings, _, _, _ = self._coerce_fields(
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

        def grounding_hook(fields: list[ExtractedField]) -> list[dict[str, Any]]:
            return self._verify_vision_grounding(
                fields=fields,
                schema=schema,
                images=images,
                image_format=image_format,
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
            post_coerce_hook=grounding_hook,
        )

    def _verify_vision_grounding(
        self,
        *,
        fields: list[ExtractedField],
        schema: FormSchema,
        images: tuple[bytes, ...],
        image_format: ImageFormat,
    ) -> list[dict[str, Any]]:
        """Second-pass grounding check on vision-mode extractions.

        Runs a fresh ``invoke_multimodal`` call with the image plus a
        verification prompt asking the model to confirm whether each
        candidate ``value`` is genuinely present in the image. Returns a
        list of ``{"name", "value"}`` dicts for fields the model marked
        ``present=false`` — these surface as ``vision_grounding_failures``
        in the retry loop and trigger a re-extraction.

        Response shape is enforced via Pydantic (``_GroundingResponse``);
        any deviation (missing key, wrong type, extra keys) is treated
        as a parse failure and fails open. A future migration to
        ``BedrockModel.structured_output`` would force the shape at the
        model layer instead of post-parse — see CHANGELOG follow-on.
        Fail-open rationale: a verifier that can't speak the contract
        should not invalidate every extraction. The first-pass schema
        validation already constrains the result; grounding is
        defence-in-depth.
        """
        candidates = [
            {"name": f.name, "value": f.value} for f in fields if f.value not in (None, "")
        ]
        if not candidates:
            return []
        settings = self._bundle.settings
        prompt = self._bundle.prompt_renderer.verify_grounding(schema=schema, candidates=candidates)
        with _TRACER.start_as_current_span("extraction.vision_grounding") as gspan:
            gspan.set_attribute("extraction.candidate_count", len(candidates))
            try:
                raw = invoke_multimodal(
                    region=settings.aws_region,
                    model_id=settings.bedrock_model_id,
                    prompt=prompt,
                    images=images,
                    image_format=image_format,
                    max_tokens=settings.bedrock_max_tokens,
                    temperature=settings.bedrock_temperature,
                    top_p=settings.bedrock_top_p,
                )
            except (BotoCoreError, ClientError) as exc:
                # Transport failure (throttle, validation, networking) on the
                # verifier call: fail open. The extraction's first-pass schema
                # validation still constrains the result; we'd rather ship
                # unverified than block on a transient verifier outage.
                LOGGER.warning("vision-grounding verifier transport failure: %s", exc)
                gspan.set_attribute("extraction.grounding_transport_failed", True)
                return []
            try:
                payload = self._parse_json(raw)
            except ExtractionError as exc:
                LOGGER.warning("vision-grounding verifier returned malformed JSON: %s", exc)
                gspan.set_attribute("extraction.grounding_parse_failed", True)
                return []
            try:
                response = _GroundingResponse.model_validate(payload)
            except ValidationError as exc:
                LOGGER.warning(
                    "vision-grounding verifier response did not match schema: %s",
                    exc.errors(),
                )
                gspan.set_attribute("extraction.grounding_parse_failed", True)
                return []
            value_by_name: dict[str, object] = {f.name: f.value for f in fields}
            failures: list[dict[str, Any]] = [
                {"name": item.name, "value": value_by_name[item.name]}
                for item in response.groundings
                if not item.present and item.name in value_by_name
            ]
            gspan.set_attribute("extraction.grounding_failures", len(failures))
            return failures

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
        post_coerce_hook: Callable[[list[ExtractedField]], list[dict[str, Any]]] | None = None,
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
            previous_value_anchor_failures: list[dict[str, Any]] = []
            previous_vision_grounding_failures: list[dict[str, Any]] = []

            for attempt in range(self._max_retries + 1):
                with _TRACER.start_as_current_span(
                    "extraction.attempt", attributes={"attempt": attempt}
                ) as sub_span:
                    if attempt == 0:
                        raw = invoke_fn(initial_prompt)
                    else:
                        span.set_attribute("extraction.retry_attempt", attempt)
                        LOGGER.warning(
                            "Re-prompting after validation issues (mode=%s): "
                            "errors=%d citation_fails=%d validator_fails=%d "
                            "value_anchor_fails=%d vision_grounding_fails=%d",
                            mode,
                            len(previous_validation_errors),
                            len(previous_citation_failures),
                            len(previous_validator_failures),
                            len(previous_value_anchor_failures),
                            len(previous_vision_grounding_failures),
                        )
                        retry_prompt = self._bundle.prompt_renderer.retry(
                            schema=schema,
                            document_text=document_text,
                            validation_errors=previous_validation_errors,
                            citation_failures=previous_citation_failures,
                            validator_failures=previous_validator_failures,
                            value_anchor_failures=previous_value_anchor_failures,
                            vision_grounding_failures=previous_vision_grounding_failures,
                        )
                        raw = invoke_fn(retry_prompt)

                    try:
                        payload = self._parse_json(raw)
                    except ExtractionError as exc:
                        previous_validation_errors = [f"Could not parse JSON from response: {exc}"]
                        previous_citation_failures = []
                        previous_validator_failures = []
                        previous_value_anchor_failures = []
                        previous_vision_grounding_failures = []
                        sub_span.add_event("attempt.failed", {"reason": "json-parse-failed"})
                        continue

                    (
                        fields,
                        warnings,
                        citation_failures,
                        validator_failures,
                        value_anchor_failures,
                    ) = self._coerce_fields(payload, schema, document_text=document_text)
                    # Vision-mode grounding (ADR-0011): a second model call
                    # confirms each candidate value is actually present in the
                    # image. Ungrounded fields trigger a retry with vision-
                    # specific instructions in the retry prompt.
                    vision_grounding_failures: list[dict[str, Any]] = (
                        post_coerce_hook(fields) if post_coerce_hook is not None else []
                    )
                    sub_span.set_attribute("attempt.warnings", len(warnings))
                    sub_span.set_attribute("attempt.citation_failures", len(citation_failures))
                    sub_span.set_attribute("attempt.validator_failures", len(validator_failures))
                    sub_span.set_attribute(
                        "attempt.value_anchor_failures", len(value_anchor_failures)
                    )
                    sub_span.set_attribute(
                        "attempt.vision_grounding_failures", len(vision_grounding_failures)
                    )

                    fatal = [w for w in warnings if w.startswith("MISSING_REQUIRED:")]
                    is_last_attempt = attempt == self._max_retries
                    # Value-anchor and vision-grounding failures are fatal —
                    # they indicate fabricated values (text-mode citation
                    # mismatch or vision-mode grounding miss). See ADR-0011.
                    has_anchor_failure = bool(value_anchor_failures)
                    has_grounding_failure = bool(vision_grounding_failures)
                    if (
                        not fatal and not has_anchor_failure and not has_grounding_failure
                    ) or is_last_attempt:
                        # On retry-budget exhaustion with grounding/anchor
                        # failures still open, surface the per-field misses
                        # to the caller via warnings so downstream consumers
                        # can route those records (a system whose explicit
                        # goal is automation-no-human-review needs a JSON
                        # signal, not just a span attribute). See ADR-0011.
                        if is_last_attempt:
                            warnings = list(warnings)
                            warnings.extend(
                                f"UNGROUNDED:{f['name']}" for f in vision_grounding_failures
                            )
                            warnings.extend(
                                f"UNANCHORED:{f['name']}" for f in value_anchor_failures
                            )
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
                    previous_value_anchor_failures = value_anchor_failures
                    previous_vision_grounding_failures = vision_grounding_failures

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
        list[dict[str, Any]],
    ]:
        """Project ``payload['fields']`` onto the schema, returning five lists.

        Citation verification is skipped when ``document_text`` is empty
        (vision mode); ``citation_failures`` and ``value_anchor_failures``
        will always be empty in that case and per-field
        ``citation_verified`` stays ``False``.

        ``value_anchor_failures`` catches the schema-confusion case
        flagged in ADR-0011: the model returned a verbatim
        ``source_excerpt`` (passes :func:`verify_excerpt`) but invented
        a ``value`` that is nowhere inside that excerpt.
        """
        warnings: list[str] = []
        citation_failures: list[dict[str, Any]] = []
        validator_failures: list[dict[str, Any]] = []
        value_anchor_failures: list[dict[str, Any]] = []

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
            # Capture the original (pre-normalisation) value so the citation
            # value-anchor check below compares the model's *emitted* string
            # against the source excerpt, not the validator's canonical form
            # (which legitimately differs, e.g. date "Jan 15, 2026" -> "2026-01-15").
            raw_value = value
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
                # Even when the excerpt itself appears verbatim in the doc, the
                # emitted value must be anchored inside that excerpt (otherwise
                # the model fabricated the value while citing real surrounding
                # text). Non-string values skip this check inside
                # `value_anchored_in_excerpt`; we additionally skip for field
                # types whose validators legitimately rewrite the surface form
                # (e.g. DATE: doc says "January 15, 2026", value="2026-01-15").
                if (
                    cc.verbatim
                    and spec.type not in _NORMALISING_STRING_TYPES
                    and not value_anchored_in_excerpt(raw_value, excerpt)
                ):
                    citation_verified = False
                    value_anchor_failures.append(
                        {
                            "name": spec.name,
                            "excerpt": excerpt,
                            "value": raw_value,
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
        return out, warnings, citation_failures, validator_failures, value_anchor_failures
