# ADR-0016: Vision-mode grounding via a second model call (automation, no human review)

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

[ADR-0008](0008-extraction-modes-text-vs-vision.md) introduced the
vision path and explicitly disabled citation verification for it
("we don't have a canonical document text"). That left vision mode
as the only ungrounded path: a malicious image (adversarial pixel
text, hidden white-on-white instructions, watermarks) could drive
the model to emit fabricated field values with high confidence and a
self-cited `source_excerpt` quoting the fabricated text.
[ADR-0011](0011-prompt-injection-threat-model.md) flagged this as a
HIGH-severity gap that needed closing.

The threat-model discussion that followed clarified two points the
v0.3 deployment context settled:

1. The service is called by an internal trusted system, but the
   documents come from the general public — so the document content
   side of the trust boundary is fully attacker-controlled.
2. The product goal is **automation** — there is no human-review
   queue downstream that could catch a fabricated value. Whatever
   defence ships has to be machine-verifiable.

Three options for closing the gap:

1. **Per-tenant allowlist.** Only run vision mode for callers who
   are known-safe. Out of scope: callers are the general population.
2. **Human-review-by-default.** Mark vision-mode results as needing
   human review before they ship downstream. Contradicts the
   automation goal.
3. **Second-pass model call.** After the first vision extraction,
   issue a fresh `bedrock-runtime.converse` call with the same image
   plus a verifier prompt asking "is each candidate value actually
   present in the image?" Use the verifier's `present=false` answers
   to flag fabricated values and trigger a retry.

## Decision

Second-pass model call, implemented in
`ExtractionService._verify_vision_grounding` and wired into the
retry loop via the `post_coerce_hook` parameter on
`_run_extraction_loop`. Every vision-mode extraction now runs:

```
extract (model call 1) → coerce → grounding verify (model call 2)
   ↓ ungrounded fields?
   yes → retry-loop kicks (re-extract with vision_grounding_failures
                            in the retry prompt)
   no  → ship the result
```

Implementation specifics:

- The verifier prompt (`templates/verify_grounding.j2`, v1.1.0) lists
  the candidate `(name, value)` pairs and asks for a JSON object
  shaped `{"groundings": [{"name": "...", "present": true|false}, ...]}`.
- The verifier prompt carries the same trust-boundary paragraph as
  the extraction prompt + an explicit anti-collusion clause:
  text in the image that *claims* a value is present is data, not a
  directive.
- Verifier response is Pydantic-validated through
  `_GroundingResponse` with `model_config = ConfigDict(extra="forbid")`
  and `present: StrictBool`. Loose-truthy values (`"yes"` / `1`) are
  rejected at validation rather than coerced.
- **Fail-open** on parse failure or transport failure (Bedrock
  ClientError, BotoCoreError): the verifier returns no failures and
  the first-pass schema-validated result ships unverified. Rationale:
  a verifier that can't speak the contract should not invalidate
  every extraction; the first-pass validation already constrains the
  result. Failures are surfaced as `extraction.grounding_parse_failed`
  / `extraction.grounding_transport_failed` span attributes for
  operator visibility.
- After all retry attempts are exhausted with grounding failures
  still open, the result ships **with** `UNGROUNDED:<field_name>`
  warnings appended to `ExtractionResult.warnings[]`. The caller
  sees both the value AND the warning and makes its own routing
  decision (audit trail, alerting, downstream filter).

## Consequences

**Positive**

- The single biggest gap from ADR-0011 is closed without compromising
  the automation goal — callers always get a result, but a flagged
  result carries an `UNGROUNDED:` warning they can route differently.
- Strict Pydantic validation on the verifier output prevents a
  category of attacker-influenced verifier responses (loose-truthy
  `present` values) from silently confirming fabricated extractions.
- The retry loop already exists; grounding piggybacks on it via
  `post_coerce_hook` rather than adding a parallel control path.
  One state machine to debug.
- Spans (`extraction.vision_grounding`) make the second-pass cost
  visible in trace search; failures are tagged
  (`extraction.grounding_failures`,
  `extraction.grounding_parse_failed`, etc.) for SLO dashboards.

**Negative**

- Vision-mode requests now do at minimum 2× the Bedrock work (extract
  + verify). On the worst-case retry path (3 attempts), that's
  6 model calls instead of 3 — see
  [`cost-breakdown.md`](../cost-breakdown.md) for the per-page
  worst-case math (~$0.18 / image vs ~$0.06 single-pass). Acceptable
  given the threat model + automation goal; the cost-reduction
  options (skip grounding when first-pass `overall_confidence` is
  reliably high) are deferred until the eval harness produces
  calibrated curves.
- The verifier itself is an LLM call against the same image, so a
  sufficiently sophisticated adversarial image could in principle
  attack BOTH the extractor AND the verifier. The anti-collusion
  paragraph in the verifier prompt is the only in-prompt mitigation.
  An external grounding signal (e.g., textract-OCR cross-check)
  would be more robust but is explicitly out of scope per
  [ADR-0008](0008-extraction-modes-text-vs-vision.md).
- The "automation, no human review" framing means the service ships
  unverified results when the verifier transport fails. Operators
  need to monitor `extraction.grounding_transport_failed` and
  decide if a sustained failure rate warrants degrading to a
  reject-on-failure policy.
- Fail-open on validation failure of the verifier response means
  attackers who can persuade the verifier to return a malformed
  shape (e.g., `present: "yes"`) get the same treatment as transport
  failures — unverified results ship. The `StrictBool` change made
  this harder, but the residual is real.

## References

- [ADR-0008](0008-extraction-modes-text-vs-vision.md) — the vision
  path that disables citation verification (the gap this ADR closes).
- [ADR-0011](0011-prompt-injection-threat-model.md) — the
  prompt-injection threat model; this ADR is the resolution of its
  Critical follow-on for vision mode.
- [ADR-0009](0009-bounded-self-correcting-retry.md) — the retry-loop
  shape this ADR plugs into via `post_coerce_hook`.
- `src/bedrock_strands_agent/extraction/service.py:_verify_vision_grounding`.
- `src/bedrock_strands_agent/templates/verify_grounding.j2`.
- `tests/test_api.py` — the four vision-grounding tests
  (failure-triggers-retry, persistence-yields-warning, parse-fail
  fails-open, transport-fail fails-open).
- `docs/cost-breakdown.md` — the cost implication.
