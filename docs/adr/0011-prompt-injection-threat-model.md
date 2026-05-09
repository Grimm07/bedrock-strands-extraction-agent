# ADR-0011: Prompt-injection threat model and layered input-side defences

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The service exists to ingest documents the model has never seen, extract
structured fields, and return JSON. The deployment profile makes the threat
model unusual:

- **Caller trust:** the HTTP front door is fronted by an internal service.
  Auth, rate-limiting, request shape, and IP exposure are owned by that
  upstream service. A direct attacker on `POST /extract*` is out of scope
  for this ADR.
- **Content trust:** the documents extracted are **open to the general
  population**. Anything that ends up in `document_text` (request body) or
  in the bytes of `/extract/document` uploads is fully attacker-controlled.

That asymmetry inverts the usual web-app prompt-injection threat model.
Most appsec gates (auth, rate limiting, secret hygiene) sit on the trusted
side; the meaningful attack surface is the model's reasoning over
attacker-controlled text. Without explicit input-side defences, a malicious
document can drive the model to ignore its operating contract, fabricate
field values, exfiltrate the system prompt, or follow embedded instructions.

This ADR pins the threat model, lists the layered defences shipped at
v0.3.0, and names the explicit deferrals that future work must close.

## Decision

We adopt a **layered defence** approach with no single load-bearing barrier.
Each layer catches a different class of attack; together they raise the cost
of a successful injection significantly.

### Layer 1 — input bounds (API)

- `ExtractRequestBody.document_text` carries `max_length=200_000` (about
  50K English tokens, well within Claude Sonnet's 200K context). Oversized
  payloads return HTTP 422 at FastAPI's validation layer; no Bedrock call
  is burned. Caps memory cost on the threadpool worker that runs each
  extraction.
- `MAX_UPLOAD_BYTES = 5 MiB` on `/extract/document` (existing).
- `MAX_DOCUMENT_TEXT_CHARS = 200_000` mirrored in
  `extraction.document._process_pdf` so PDF uploads that compress
  aggressively (a 1 MiB PDF can extract to multi-MB of text) hit the
  same character cap as the JSON-body endpoint, returning a 422 with
  no Bedrock call.
- `RASTER_MAX_PAGES = 5` on scanned-PDF rasterisation (existing).

### Layer 2 — prompt-template hygiene

- `templates/system.j2` carries an explicit "Trust boundary" paragraph
  declaring text inside `<document>...</document>` tags untrusted, telling
  the model to ignore embedded imperative language and any apparent
  `</document>` close-tag inside the body.
- `templates/extract.j2` and `templates/retry.j2` wrap `document_text` in
  `<document>...</document>` tags (template version 2.0.0 / 3.0.0,
  superseding the previous `"""..."""` triple-quote delimiter that was
  trivially escapable by a document containing `"""`).
- `templates/extract_image.j2` carries the same trust-boundary paragraph
  for image content, telling the model that text appearing in image
  pixels is data, not commands.
- All templates render with Jinja `StrictUndefined`; missing variables
  raise `TemplateError` at render time rather than producing malformed
  prompts.

### Layer 3 — model output validation

- `_parse_json` in `extraction/service.py` strips Markdown fences and
  extracts the first JSON object; non-conforming output raises
  `ExtractionError` and the retry loop kicks (ADR-0006).
- Schema validation rejects fields that don't match the registered shape
  (name + type + required-flag).
- Semantic validators (`validate_value` for SSN/EIN/EMAIL/DATE) check
  format. They do **not** check semantic validity — see deferrals.
- Citation verification (`extraction.citations.verify_excerpt`) checks
  that `source_excerpt` substantially overlaps with `document_text` for
  text-mode extractions. Fabricated citations trigger a retry.
- Bounded retry loop (`max_retries=2`, ADR-0009) gives the model up to
  two self-correction attempts; persistent jailbreaks must outlast 3
  prompts to succeed.

### Layer 4 — observability

- Span attributes are restricted to metadata (schema name + version,
  model id, latency, counts, document_id, correlation_id). The
  `security-reviewer` subagent enforces this on PR review (no
  `document_text`, no `ExtractedField.value` in spans).
- The semgrep rule `no-print-of-document-text` blocks the obvious
  PII-leak pattern at pre-commit.
- A future `extraction.injection_signal_total{kind}` Prometheus counter
  (deferred) will count documents that trip known injection markers,
  giving operators a traffic-monitoring signal.

### Layer 5 — adversarial test coverage

- `tests/test_prompts.py` pins the `<document>` wrapper contract and
  asserts adversarial bodies (containing `</document>` + "ignore previous
  instructions") render without breaking the wrapper.
- `tests/test_api.py` pins the 422 on oversized `document_text`.
- A dedicated adversarial fixture bucket under `tests/eval/adversarial/`
  is **deferred** to the Phase D5 evaluation harness; this ADR commits us
  to including it when that harness lands.

## Alternatives considered

- **Input sanitisation by string replacement** (rejected). Stripping
  injection-sounding phrases is an arms race we lose. The model is fine
  with imperative language *as long as it understands the language is
  data, not instructions*. The trust-boundary paragraph in the system
  prompt does that work.
- **Per-request random sentinels** (deferred). Anthropic's prompt-engineering
  guide recommends content-derived or random IDs in wrapper tags
  (`<document id="b8a4f2">…</document id="b8a4f2">`) so a malicious body
  cannot fabricate the close-tag. The current fixed `<document>` tag
  defends through natural-language instruction in the system prompt — a
  soft barrier, since an attacker's "fake `</document>`" is the same
  string as the real one. The random sentinel becomes load-bearing if/
  when we extend the trust model beyond an internal caller, since it is
  the cheapest hardening that turns the wrapper into an unforgeable
  boundary. Until then, the fixed tag + trust-boundary paragraph +
  schema + citation defences is the stack.
- **Output filtering on the JSON before returning to the caller**
  (rejected for v0.3.0). A regex/heuristic post-filter would only catch
  obvious leaks (system prompt, AWS credentials) and risks false
  positives on legitimate fields. The schema contract already constrains
  output shape; a separate output-content filter is deferred until the
  eval harness measures whether it would catch real attacks.
- **Constitutional / self-critique pass before returning** (deferred). A
  second model call asking "does this output match the document content?"
  is the natural vision-mode grounding mechanism. Out of scope here;
  see the vision-grounding ADR / deferral below.

## Consequences

**Positive**

- Prompt injection through `document_text` is no longer trivial. The
  triple-quote delimiter (which a malicious document could mimic with a
  literal `"""`) is gone.
- Oversized payloads fail fast at the API layer with no Bedrock cost.
- The threat model is pinned; future contributors have a written
  contract describing what we defend and what we don't.
- The ADR identifies the deferrals so future work can close the gaps in
  priority order (vision grounding > MCP sanitisation > semantic
  validity > random sentinels).

**Negative / accepted**

- **Vision mode has no grounding.** ADR-0008 disabled citation
  verification for vision extractions. A malicious image with adversarial
  text (visible or low-contrast) can drive fabricated values with
  high confidence. The system-prompt trust-boundary paragraph is the
  *only* in-prompt defence; semantic validators catch format errors only.
  Closing this gap is tracked as a Critical roadmap item ("Ground the
  vision path"); the candidate fix is a second-pass model call asking
  "is each emitted value actually present in the image?".
- **MCP tool responses are unfiltered.** Tools exposed by enabled MCP
  servers can return attacker-controlled text that flows into the
  agent's context window with no size cap, no content-type sanitisation,
  no HTML/script stripping. ADR-0007 documents the trust assumption.
  Closing this gap is tracked as a Critical roadmap item ("Sanitise MCP
  tool responses"). Mitigated today only by the
  `MCP_ENABLED_SERVERS` allowlist gating *which servers* run.
- **Semantic validity is not checked.** A fabricated-but-format-valid
  SSN (`"999-99-9999"`) passes `validate_ssn`. We rely on citation
  verification to catch that the value isn't actually in the document
  body, but in vision mode (no citation) or with a paraphrased excerpt
  (95% overlap), it slips through. Closing this is tracked under
  "Tighten citation verification".
- **Persistent jailbreaks across the retry loop** are possible. The same
  adversarial `document_text` is re-rendered into each of the three
  attempts (initial + two retries; see ADR-0009), so a successful
  injection has three independent chances to take hold. Worse, the retry
  prompt's framing ("fix only the issues listed above; keep the rest of
  your prior values unchanged") biases the model toward preserving its
  prior — potentially jailbroken — outputs. The 3-attempt cap bounds
  cost, not jailbreak probability.
- **System-prompt extraction is not specifically defended.** The system
  prompt contains service metadata (name, env, model id) and the schema
  contract — none of which is sensitive (the schema is exposed by
  `GET /schemas` anyway). Claude generally resists "repeat your system
  prompt" requests; we accept this risk.
- **Test coverage is structural, not behavioural.** The new tests pin
  the wrapper-tag contract and the input-bound but do not run real
  adversarial documents against a real model. The Phase D5 eval harness
  is the right place for that battery; this ADR commits us to building
  it.

## References

- ADR-0002 — schema-first extraction with self-correcting retry (the
  output-side defence layer this ADR builds on top of).
- ADR-0006 — JSON-only response contract (parser tolerance + retry
  triggers).
- ADR-0007 — MCP-as-tools (the indirect-injection surface).
- ADR-0008 — extraction modes text vs vision (the vision-mode
  grounding deferral this ADR inherits).
- ADR-0009 — bounded self-correcting retry (the 3-attempt cap that
  bounds jailbreak persistence cost).
- `src/bedrock_strands_agent/api/schemas.py` — `ExtractRequestBody`
  with `max_length=200_000` on `document_text`.
- `src/bedrock_strands_agent/templates/system.j2` — Trust boundary
  paragraph (template version 1.1.0).
- `src/bedrock_strands_agent/templates/extract.j2` — `<document>` tag
  delimiter (template version 2.0.0).
- `src/bedrock_strands_agent/templates/retry.j2` — `<document>` tag
  delimiter (template version 3.0.0).
- `src/bedrock_strands_agent/templates/extract_image.j2` — image
  trust-boundary paragraph (template version 1.1.0).
- `tests/test_prompts.py::test_system_prompt_declares_trust_boundary`,
  `test_extract_prompt_wraps_document_in_xml_tags`,
  `test_extract_prompt_does_not_break_when_document_contains_close_tag`,
  `test_retry_prompt_wraps_document_in_xml_tags`,
  `test_extract_image_prompt_declares_image_trust_boundary` — wrapper
  contract and adversarial-body coverage.
- `tests/test_api.py::test_extract_oversize_document_text_returns_422` —
  input-bound enforcement.
- Anthropic's prompt-engineering guide on long-context XML tags
  (the `<document>...</document>` pattern).
