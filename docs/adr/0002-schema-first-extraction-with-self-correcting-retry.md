# ADR-0002: Schema-first extraction with self-correcting retry

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

Form extraction is a JSON-shape-critical task. A free-text response from a
Bedrock model is unusable downstream — every consumer expects a known field
set with stable types. LLMs *will* hallucinate fields, drop required ones,
emit Markdown fences, or interpolate prose around the JSON.

We need:

- A single source of truth for which fields are extractable per form type.
- A way to *constrain* the model's output to that shape, not just hope.
- A way to *recover* when a single attempt produces invalid output, without
  paying unbounded latency or cost.

## Decision

Three-part design:

1. **Schema registry** (`src/bedrock_strands_agent/extraction/schemas/`) holds
   `FormSchema` objects (Pydantic v2, `extra="forbid"`) that enumerate every
   supported form's fields, types, required-flags, regex patterns, and (in
   Phase C) few-shot examples. The agent has no ability to invent a schema; if
   a caller asks for an unknown name, the API returns 404.
2. **Per-request prompt rendering** via Jinja2 templates with `StrictUndefined`
   and `autoescape` disabled (text rendering). The schema and document are
   injected into `extract.j2`. The system prompt (`system.j2`) declares the
   exact JSON contract: `{"fields": [...]}` — no prose, no fences.
3. **Post-hoc validation + one self-correcting retry** in
   `ExtractionService.extract()`. After the model responds, we strip code
   fences, parse JSON, project onto the schema with `_coerce_fields`, and
   collect warnings (missing required fields, pattern mismatches, etc.). If
   any are *fatal* and the attempt budget is not exhausted, we re-prompt with
   `retry.j2` carrying the precise errors back to the model.

In Phase C this expands to: Pydantic `structured_output_model=` constraint
(ADR-0004), citation verification (excerpt must appear verbatim in the
document), validator-driven coercion (SSN/EIN/email/date semantic checks),
weighted confidence calibration (ADR-0004), and an optional reflection /
self-consistency layer.

## Consequences

**Positive**

- Bounded latency cost: at most one retry per request in v0.1, two in v0.2 —
  worst case is two extra Bedrock invocations.
- Wire-format stability: callers see a typed `ExtractionResult` regardless of
  what the model emits.
- Errors carry forward into the retry context, so the second attempt knows
  exactly what failed — not "try again."
- The schema registry is a natural extension point for new forms (W-9, 1099,
  invoice today; K-1 and others to come).

**Negative**

- Schema authoring is an upfront cost — adding a new form requires defining
  its fields *and* contributing few-shot examples and golden eval cases
  (Phase D).
- The model is told to use validator tools (`validate_ssn`, `normalize_date`)
  but does not always do so. Phase C wires those validators into the service
  layer post-response so we don't depend on prompt discipline alone.
- Over-strict schema (e.g. an unrealistic regex) causes false-negative
  validation failures and wastes a retry. Mitigation: the schema validator
  tests run on every PR and the eval harness (Phase D5) catches regressions.

## References

- `src/bedrock_strands_agent/extraction/service.py:90-136` — retry loop.
- `src/bedrock_strands_agent/templates/{system,extract,retry}.j2`.
- `src/bedrock_strands_agent/extraction/schemas/examples.py` — current schemas.
- ADR-0001 — Strands SDK choice.
- ADR-0004 — confidence calibration formula (Phase C).
