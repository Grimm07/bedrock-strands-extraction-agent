# ADR-0008: Two extraction paths — text vs Bedrock multimodal vision (no separate OCR engine)

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The v0.1 service exposed only `POST /extract`, which accepts pre-extracted
`document_text`. Real callers also want to send raw documents. ROADMAP
deferred the upload endpoint to v0.3 with the note "future
`/extract/upload` accepts multipart PDF/image; existing deps `pypdf>=5.1`,
`pillow>=11.0` cover this".

Pulling that work forward raised the architectural question: what does
"the OCR step" look like? Three reasonable shapes:

1. **Run tesseract first** in-process and feed text to the existing
   path. Adds a system package and a layout-blind extractor.
2. **Run AWS Textract first**, exploit its KV/TABLES output, then map
   to our schema with the LLM (or rule-based code). Strong on tabular
   data, costs another network hop and another invoice line.
3. **Skip the dedicated OCR step and use the Bedrock multimodal
   model that already ships with the service.** The model reads the
   image directly and emits the same JSON contract the text path emits.

## Decision

Pull `/extract/document` into v0.2 as a multipart upload endpoint.
Auto-route by content:

- **PDF with embedded text** → extract via `pypdf` → existing text path.
  Cheap, deterministic, ~zero added latency vs the existing text route.
- **Image (PNG / JPEG / WebP / GIF)** → invoke a Bedrock multimodal model
  (Claude Sonnet by default) via the **Bedrock Converse API directly**.
  No separate OCR engine.
- **Scanned PDF (no embedded text)** → reject with 422 plus a clear
  message asking the caller to render it to an image client-side. PDF
  rasterisation deps (`pypdfium2`, `pdf2image+poppler`, etc.) intentionally
  deferred to v0.3 to keep the install footprint small.

The vision call is **not** routed through `Strands.Agent`; the Agent
wrapper's tool dispatch is not load-bearing for image-only input
because semantic validators run server-side post-response (see C4).
The same `tenacity` retry wrapper from A11 protects both call shapes.

Citation verification is disabled in vision mode (we don't have a
canonical document text). The `citation_verified` flag stays False and
the field-level confidence loses its ×1.1 bonus from ADR-0004.

## Why not tesseract

- Layout-blind by design; throws away spatial relationships our schema
  cares about.
- Per-character (not per-field) confidence; doesn't compose with our
  weighted overall_confidence (ADR-0004).
- Brittle on form artefacts (stamps, signatures, rotations, bleed-through).
- Adds a system package (`libtesseract`, language data) and a Python
  binding to the container — meaningful CVE / cold-start surface.
- No semantic grounding; cannot pick the SSN line out of a TIN field
  containing both an SSN and an EIN.

## Why not AWS Textract (as a *required* step)

Textract is a real alternative — strong on tabular layouts, native KV
extraction, AWS-native auth — and the service can be **composed with
it** by the caller (run Textract, send the cleaned text to `/extract`).
We rejected mandating it in the pipeline because:

- Schema mismatch: Textract's KV pairs are whatever the document
  carries; our schema names are fixed. We'd still need an LLM (or
  hand-written matcher) to align `Tax ID` → `ssn` xor `ein`.
- Cost stacks (Textract $/page **plus** Bedrock $/token) and latency
  stacks (Textract is several seconds per page on top of the LLM
  call), so p95 < 3 s becomes harder to meet at the per-page envelope.
- Bounded format support (PDF/PNG/JPEG/TIFF) gives no advantage over
  Bedrock multimodal vision.
- Requires a second IAM scope (`textract:*`) and a second AWS service
  in the dependency tree.

For workloads where Textract genuinely wins (massive volume, fixed
schema, tabular data) the service can be deployed *behind* a Textract
front-end without code changes — the text endpoint accepts whatever
text the caller produces.

## Why Bedrock multimodal vision

- The same Bedrock model the service already calls. No new IAM scope,
  no second vendor, no second pricing line. Re-uses the existing
  `aws_iam_role_policy` for `bedrock:InvokeModel`.
- Re-uses the same retry wrapper (`invoke_with_retry`), the same
  redaction filter (Phase D4), the same telemetry pipeline (Phase D1).
- Layout-aware: the model sees the page as a human reader would, so
  field ordering and spatial cues are preserved without us writing a
  layout reconstructor.
- Single Converse call returns the schema-shaped JSON directly — no
  alignment step from upstream KV labels to our schema.

## Why call Converse directly (skip `Strands.Agent`)

- The agent's Strands tools are server-side helpers; their semantic
  checks already run via `extraction.validators.validate_value`
  regardless of whether the model invoked the tool inline. There is
  no behavioural loss from skipping tool dispatch.
- The Bedrock Converse API surface is well-documented, stable, and
  easier to mock for unit tests than a multi-turn Agent loop.
- A future Strands release with a first-class multimodal helper can
  replace `extraction.multimodal.invoke_multimodal` without changing
  the public endpoint or the wire-format response.

## Consequences

**Positive**

- One pane of glass for callers: a single endpoint that handles both
  pre-extracted text (via PDF auto-detect) and raw images.
- The accuracy ceiling is the same as the LLM's vision capability —
  we don't lose nuance to a layout-blind OCR pass.
- No new system packages, no new AWS service, no new IAM scope.

**Negative**

- Per-image cost is materially larger than per-string-of-text cost
  (≈ 5 × input tokens for a typical 1-page PNG scan at 200 DPI).
  The default rate-limit is unchanged; cost-sensitive workloads
  should set `RATE_LIMIT_PER_MINUTE` accordingly.
- Citation verification is degraded in vision mode (no doc text to
  verify against). `overall_confidence` loses the ×1.1 citation
  bonus on every field; we recommend keeping the `auto_approve`
  threshold at the same value (≥ 0.85) since the bonus is small.
- The vision path bypasses `Strands.Agent`, so a future requirement to
  log per-tool-call timing during model reasoning would force a refactor
  back through Strands.

## v0.3 update — server-side rasterisation of scanned PDFs

The original decision rejected scanned PDFs with a 422 asking the caller
to rasterise client-side, deferring `pypdfium2` to v0.3 to keep the
install footprint small. That follow-on now ships:

- `pypdfium2` is a hard dep (statically-linked PDFium, ~3.5 MiB binary
  wheel, no system packages, Apache-2.0 + BSD-3 licensing).
- A scanned PDF (no embedded text on any page) is rendered to PNG at
  `RASTER_DPI` (200) and routed to the existing vision path. The first
  `RASTER_MAX_PAGES` (5) pages are kept; further pages are silently
  dropped. Both constants live in
  `src/bedrock_strands_agent/extraction/document.py` and are deliberately
  not surfaced as `Settings` until a real workload asks for it.
- Mixed-content PDFs (some text, some scanned pages) continue to use the
  text path: any page with extractable text wins. Callers that need
  vision treatment for the scanned pages of a mixed PDF must split
  client-side.
- The 5 MiB upload cap (`MAX_UPLOAD_BYTES`) still applies to the *input*
  PDF; rendered PNGs do not pass back through the cap, but the page-cap
  bounds the total Converse payload in practice.

## References

- `src/bedrock_strands_agent/extraction/document.py` — input detection
- `src/bedrock_strands_agent/extraction/multimodal.py` — Bedrock Converse
  call shape
- `src/bedrock_strands_agent/templates/extract_image.j2` — vision-mode
  prompt template
- `docs/extraction-modes.md` — full design doc with model comparison
- ADR-0001 — Strands SDK choice (still holds for the text path).
- ADR-0002 — schema-first extraction (unchanged contract on both paths).
- ADR-0004 — confidence calibration (the citation bonus that vision mode
  forfeits).
