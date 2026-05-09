# Extraction modes: text vs document (vision)

## TL;DR

The service exposes two extraction paths:

| Endpoint            | Input                          | Bedrock call shape                | When to use                                                                       |
| ------------------- | ------------------------------ | --------------------------------- | --------------------------------------------------------------------------------- |
| `POST /extract`     | `document_text` (string)       | text-only Converse                | You already have clean, trustworthy text from upstream (DB, queue, prior service) |
| `POST /extract/document` | multipart PDF/image upload     | text-only OR multimodal Converse  | You have a raw file; the service detects whether to extract text or use vision    |

`/extract/document` auto-routes:

- **PDF with embedded text** → extract via `pypdf`, run text path (cheap, deterministic)
- **PDF without embedded text or image** (PNG / JPEG / WebP / GIF) → run vision path against the same Bedrock model (Claude multimodal Sonnet by default)

There is no separate OCR engine in the pipeline. The vision path sends the
image directly to a Bedrock multimodal model. This document explains why,
how it compares to running tesseract or AWS Textract first, and when you
might still want a dedicated OCR step.

---

## Why no tesseract / no Textract?

For this service's workload — heterogeneous tax forms (W-9, 1099-NEC, K-1)
and invoices, moderate volume, high accuracy required — running a separate
OCR step before the LLM **adds failure modes without solving them**:

### Tesseract

- **Layout-blind by design.** Tesseract emits a flat text blob (or
  page-segmented blobs). For a W-9, the field labels and values lose their
  spatial relationship — `Name: Acme Widgets, Inc.` may end up in the
  wrong order across columns. The LLM then has to reconstruct what the
  vision model could have read directly.
- **Confidence is per-character, not per-field.** Tesseract returns OCR
  confidence at the glyph or word level. Our service's confidence metric
  is *semantic* (per-extracted-field, weighted by validator pass + citation
  match — see ADR-0004). The two don't compose cleanly without an extra
  alignment layer.
- **Brittle on form artefacts.** Stamps, signatures, handwritten amendments,
  scan rotation, and bleed-through degrade tesseract output disproportionately
  vs a vision-language model trained on a much wider distribution.
- **Operationally heavy.** Adds a system package (libtesseract / leptonica),
  language data, and an in-process binding (pytesseract) — meaningful
  surface area for CVEs, container size, and cold-start latency.
- **No semantic grounding.** Tesseract sees pixels → characters. It cannot
  pick the SSN line out of a TIN field that contains both an SSN and an EIN.

### AWS Textract

Textract is a stronger alternative to tesseract because it models layout:

- **`AnalyzeDocument` with `FORMS` and `TABLES`** returns key-value pairs
  and table structure. For machine-printed forms this is genuinely useful
  and often a good first pass.
- **Per-line / per-field confidence** — a real number to gate on.
- **AWS-native** — same IAM model, same VPC endpoints, no cross-account
  egress.

Where Textract still leaves work for us:

- **Schema mismatch.** Textract's KV pairs are whatever it detects on the
  page. Our schema has fixed field names (`legal_name`, `ssn`, `total`).
  We still need an LLM (or hand-written matcher) to align Textract's
  `Name` → our `legal_name`, `Tax ID` → our `ssn` xor `ein`, etc.
- **Cost stacks.** Textract `AnalyzeDocument` is ~$50 per 1 000 pages
  (pricing approximate; the
  [AWS Textract pricing page](https://aws.amazon.com/textract/pricing/) is
  authoritative). Bedrock multimodal vision has its own per-token cost.
  Running both means paying both.
- **Latency stacks.** Textract `AnalyzeDocument` is synchronous but
  typically several seconds per page; chaining it before a Bedrock call
  makes p95 > 3 s difficult to hold.
- **Bounded format support.** Textract handles PDF, PNG, JPEG, TIFF.
  Same as Bedrock multimodal — no advantage there.

For our workload, Bedrock multimodal vision **does the OCR and the
schema-aware extraction in a single Converse call**, which:

- Eliminates the alignment step entirely (the model maps page content to
  our schema directly via the prompt).
- Carries layout context implicitly (the model sees the page as humans do).
- Re-uses the same telemetry, retry wrapper, redaction filter, and
  validation pipeline as the text path.
- Keeps the cost-per-page within an envelope we can predict.

We do **not** assert that Bedrock vision is the fastest or cheapest path
in the abstract. For a different workload — say, ten million receipts a
day with a fixed key set — Textract+rule-based-mapping is often better.
See "[When you might still want OCR](#when-you-might-still-want-ocr)"
below.

---

## Bedrock multimodal models — factual comparison

This section reports what each Bedrock-hosted model accepts on its
multimodal API surface. It does *not* rank accuracy. Public benchmarks for
form-extraction tasks vary widely; the only reliable signal for your
workload is to run the layered eval harness (Phase D5/D6/D7) against your
own labelled dataset. The table is current as of 2026-05-09; AWS publishes
authoritative specs in the
[Bedrock model catalog](https://docs.aws.amazon.com/bedrock/latest/userguide/models-supported.html).

| Model                         | Image input | Image formats          | Images per request (Converse) | Approx. context  | Approx. input price (per M tokens) | Approx. output price (per M tokens) | Other modalities  |
| ----------------------------- | ----------- | ---------------------- | ----------------------------- | ---------------- | ---------------------------------- | ----------------------------------- | ----------------- |
| Anthropic Claude Sonnet 4.6   | yes         | PNG / JPEG / GIF / WebP | up to 20                      | 200 k            | $3.00                              | $15.00                              | text only besides image |
| Anthropic Claude Haiku 3.5    | yes         | PNG / JPEG / GIF / WebP | up to 20                      | 200 k            | $0.80                              | $4.00                               | text only besides image |
| Amazon Nova Pro               | yes         | PNG / JPEG             | several (page-level)          | 300 k            | $0.80                              | $3.20                               | image + video (≤ 30 s) |
| Amazon Nova Lite              | yes         | PNG / JPEG             | several                       | 300 k            | $0.06                              | $0.24                               | image + video |
| Amazon Nova Micro             | no          | n/a                    | n/a                           | 128 k            | $0.035                             | $0.14                               | text only |
| Meta Llama 3.2 90B Vision     | yes         | PNG / JPEG             | one per turn (typical)        | 128 k            | varies (open-weights pricing)      | varies                              | text + single image |
| Meta Llama 3.2 11B Vision     | yes         | PNG / JPEG             | one per turn (typical)        | 128 k            | varies                             | varies                              | text + single image |
| Mistral Pixtral 12B           | yes         | PNG / JPEG             | several                       | 128 k            | varies                             | varies                              | text + image |
| Amazon Titan Multimodal Embeddings | image / text input → embedding | PNG / JPEG | one              | embedding-only  | per-call                          | n/a                                  | embedding output, not text |

**Reading the table.**

- "Images per request" varies by model and by the upper bound Bedrock
  enforces for the Converse API. Always check the model card before
  designing for many images per turn.
- "Approx. price" rounded to the nearest cent and may have changed; the
  [Bedrock pricing page](https://aws.amazon.com/bedrock/pricing/) is
  authoritative and changes on AWS schedule, not ours.
- Prices are for on-demand inference. Provisioned-throughput or
  cross-region-inference profiles can change the calculation
  meaningfully (often making higher-tier models more competitive at
  scale).
- Model context windows include the image's tokenised representation;
  sending many high-resolution images can consume the budget faster
  than equivalent text would.
- Anthropic Opus tier exists but as of writing is not deployed as the
  service default; it is API-compatible with Sonnet for image input.

**Choosing a different default.** The service reads
`BEDROCK_MODEL_ID` from settings, so swapping defaults is a one-line
change at deploy time:

```bash
BEDROCK_MODEL_ID=us.amazon.nova-pro-v1:0
```

Recommend running the eval harness (Phase D5/D6) against any candidate
model on your golden set before flipping production.

---

## How the vision path works

```
caller ──── multipart upload ────▶ POST /extract/document
                                          │
                                          ▼
                          ┌── DocumentInput.from_upload ──┐
                          │                               │
                ┌─────────┴──────────┐         ┌──────────┴────────┐
                │ application/pdf    │         │ image/png|jpeg|…  │
                │ pypdf.extract_text │         │ Pillow validates  │
                └─────────┬──────────┘         └──────────┬────────┘
                          │ has embedded                  │
                          │ text?                         │
                ┌─────────▼──────────┐         ┌──────────▼────────┐
                │ yes: text path     │         │ no: vision path   │
                │ → ExtractionService│         │ → invoke_multimodal│
                │   .extract(...)    │         │   (Bedrock Converse│
                │                    │         │    + image block) │
                │                    │         │ → service.extract  │
                │                    │         │   (parsed payload)│
                └────────────────────┘         └───────────────────┘
                          │                               │
                          └─────────┬─────────────────────┘
                                    ▼
                            ExtractionResult (same wire format)
```

The vision path bypasses Strands' `Agent` wrapper and calls the Bedrock
Converse API directly. This is intentional in v0.2:

- The agent's Strands tools (`validate_ssn`, `validate_ein`, …) are not
  load-bearing in vision mode — semantic validation runs server-side
  via `extraction.validators.validate_value` (Phase C4) regardless of
  whether the model invoked the tool inline.
- A direct Converse call has a smaller surface area to reason about,
  retry around, and observe. The retry wrapper (Phase A11) wraps the
  Converse call exactly as it wraps the Agent call.

Citation verification (`extraction.citations.verify_excerpt`) is **disabled
in vision mode** because we don't have the canonical document text to
verify against. The model's `source_excerpt` is still emitted but the
`citation_verified` flag is always `False` in vision-derived results — it
gets no confidence bonus. This is deliberate: paying for the citation
bonus would require us to run an OCR pass too, which would defeat the
purpose of going vision-only.

---

## When you might still want OCR

The two-path design above suits **moderate-volume, accuracy-sensitive,
heterogeneous document workflows**. Several adjacent workloads are better
served by adding (or substituting) a dedicated OCR engine:

### 1. Massive volume, fixed schema, tight cost budget

If you're parsing millions of identical pages per day (e.g. retail
receipts, EOBs, bills of lading) and the schema is stable, running
**AWS Textract `AnalyzeDocument`** with the `FORMS` feature plus a hand-
written field mapper is cheaper and faster than per-page LLM vision.
Textract's per-page price is roughly an order of magnitude lower than a
Sonnet vision call at typical token counts, and latency is more
predictable.

A common production pattern is **Textract first → LLM fallback** when
Textract's confidence is below threshold or its KV labels don't map.

### 2. Strict tabular data

Textract's `TABLES` feature returns row × column structure with cell-level
confidence. For invoices, line-item statements, and receipts where the
*structure* is the data, a layout-aware OCR is usually the right call;
LLM vision can hallucinate a "missing" row to make the math add up.

### 3. Privacy or data-residency constraints

Tesseract runs entirely on-host with no network egress. For on-premise
deployments, air-gapped environments, or regulated workflows where even a
Bedrock call is unacceptable, a local OCR pass into a local LLM (or no
LLM at all) is the only option.

### 4. Existing pre-OCR'd pipelines

Many enterprise document archives already store text alongside the
scanned image. Re-OCR'ing wastes the upstream investment and can
*degrade* the result if the original OCR was hand-tuned for the corpus.
Use the `/extract` text endpoint for these cases.

### 5. Reliability / determinism

OCR is a deterministic function of pixels. LLM vision is not. For
audit-critical workflows where reproducibility matters more than nuance
(e.g. legal filings, regulatory submissions), an OCR + rule-based
extractor produces the same answer on the same input every run; an LLM
generally does not (even at temperature 0.0, sampling and tokenisation
can drift across model versions).

### 6. Multi-stage pipelines

You can — and often should — combine. Examples we've seen work well:

- **Textract `AnalyzeDocument` → LLM "extract only what Textract missed"**:
  cheap path covers the easy 90% of fields, LLM handles the layout-
  divergent cases.
- **Tesseract → embedding-based field router → schema-specific LLM
  extractor**: layered routing keeps token cost low for predictable
  pages.
- **LLM vision (this service) → Textract fall-back**: vision-first for
  novel layouts, Textract for the high-volume backbone. Useful when
  rolling out new schemas behind an existing Textract pipeline.

The point of this service's design is **not** "LLM vision beats
everything." It's that for *our* particular volume, accuracy, and
heterogeneity profile, the simpler Bedrock-only architecture wins on
total cost of ownership. If your profile differs, change the default
and wire OCR back in — the service's `extract` and `extract/document`
endpoints take whatever clean text or image you give them.

---

## Decision tree (for callers)

```
Do you already have clean text from upstream?
├── yes ─▶ POST /extract  (cheapest, fastest)
└── no
    │
    ├── Is your input a PDF that contains embedded text? (Native PDFs do.)
    │   ├── yes ─▶ POST /extract/document — service extracts text and
    │   │           routes to the text path automatically. Identical
    │   │           wire-format response.
    │   └── no  ─▶ PDF is a scan; convert to PNG / JPEG and use the next branch.
    │
    └── Is your input an image (PNG / JPEG / WebP / GIF, ≤ 5 MB)?
        ├── yes ─▶ POST /extract/document — vision path. Highest
        │           per-call cost, highest accuracy on novel layouts.
        └── no  ─▶ Convert first. PDF rasterisation is intentionally not
                  in this service today (deps stay slim); a Lambda or
                  client-side helper can render a page to PNG before
                  upload. We may add `pypdfium2` in v0.3 if demand
                  justifies the dep weight.
```

---

## Operational notes

- **Image size guard.** The endpoint rejects uploads > 5 MB. Bedrock
  models accept smaller; oversized files are usually scans at
  unnecessary DPI. Re-encode at 200–300 DPI before uploading.
- **Per-image cost is meaningfully larger than text.** A 1-page W-9
  scan at 200 DPI in PNG is roughly 1500 image-tokens for Claude — so
  ~ 5 × the input-token cost of the same content as plain text.
  Multiply by `self_consistency_k` if you enable it (Phase C8).
- **Vision mode disables citation verification.** Field confidence
  loses the ×1.1 citation bonus described in ADR-0004. The
  recommended `auto_approve` cutoff (`overall_confidence ≥ 0.85`)
  remains valid — you're just losing one signal.
- **Tools are not invoked in vision mode.** Server-side validators
  (`validate_value`) run on the response regardless. If you depend on
  `validate_ssn`-style call-site logging during reasoning, route to
  the text endpoint or wait for the Strands native multimodal
  integration in a future release.

---

## Related docs

- [ADR-0008](./adr/0008-extraction-modes-text-vs-vision.md) — the
  decision record this doc backs.
- [ADR-0002](./adr/0002-schema-first-extraction-with-self-correcting-retry.md)
  — the upstream contract both paths share.
- [ADR-0004](./adr/0004-confidence-calibration.md) — how the
  `citation_verified` bonus is computed (and why vision mode loses it).
- [`docs/observability.md`](./observability.md) (Phase D10) — the
  span attributes both paths emit.
