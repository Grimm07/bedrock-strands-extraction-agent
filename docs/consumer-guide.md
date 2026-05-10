# Consumer guide

A walkthrough for engineers calling this service from another system.
The code does one thing — extract structured fields from a document
according to a registered schema — but it offers four ways to call it
and three deployment modes, so this guide is the single page that
covers each path end-to-end.

For tech-stack rationale (every library and why) see
[`tech-stack.md`](tech-stack.md). For why specific design choices were
made, see the ADRs in [`adr/`](adr/README.md).

---

## What this service does (and what it doesn't)

**Does:** takes a document plus a registered schema name, returns a
typed JSON object whose `fields[]` array carries one entry per schema
field with `value`, `confidence`, and `source_excerpt`. The same
contract is exposed over HTTP, multipart upload, Server-Sent Events
streaming, and the [A2A agent-to-agent protocol](adr/0012-a2a-protocol.md).

**Does not:**

- **Persist documents.** Nothing is written to S3 or any database. The
  service is a stateless transformer; the caller owns retention.
- **Do its own OCR.** PDFs with embedded text are read via `pypdf`;
  scanned PDFs are rasterised to images server-side ([ADR-0008
  v0.3 update](adr/0008-extraction-modes-text-vs-vision.md)) and then
  routed to the Bedrock multimodal vision path. There is no
  Tesseract/Textract dependency.
- **Train or fine-tune the model.** Behaviour is steered by prompt
  templates and the schema definitions. Model swap is a one-line
  config change (`BEDROCK_MODEL_ID`).
- **Authenticate users.** AuthMiddleware validates an API key or JWT
  if you opt in (`AUTH_MODE=apikey|jwt|both`); the service does not
  manage user accounts. The threat model assumes a trusted internal
  caller in front (see [ADR-0011](adr/0011-prompt-injection-threat-model.md)).

---

## Quickstart — call it in 30 seconds

```bash
# 1. Run the service locally with stub-mode (no AWS creds needed).
uv run python scripts/_boot_with_stub.py &

# 2. Discover the registered schemas.
curl -s http://127.0.0.1:8765/schemas | jq '.schemas[].name'
# "invoice"
# "irs_w9"

# 3. Extract.
curl -s -X POST http://127.0.0.1:8765/extract \
  -H 'Content-Type: application/json' \
  -d '{"schema_name":"invoice","document_text":"INVOICE #INV-1234..."}' \
  | jq
```

Replace `_boot_with_stub.py` with `make dev` (and `127.0.0.1:8765`
with `127.0.0.1:8000`) to talk to real Bedrock — see
[Local development](#local-development) below for credentials.

---

## The four input paths

| Path | Transport | When to use |
|---|---|---|
| `POST /extract` | HTTP, JSON body | Pre-extracted text. Cheapest; happy path. |
| `POST /extract/document` | HTTP, multipart | Caller has a PDF or image file. Service auto-routes between the text path (PDF with embedded text), the vision path (image), or scanned-PDF rasterisation. |
| `POST /extract/stream` | HTTP, Server-Sent Events | Caller wants to render text deltas as the model produces them. Latency-perceptive UIs. |
| `POST /a2a/jsonrpc` | A2A JSON-RPC 2.0 | Caller is itself an agent and speaks the [A2A protocol](https://a2aproject.org). Agent-card discovery at `/.well-known/agent-card.json`. |

All four ultimately route through the same `ExtractionService.extract`
pipeline (validators, citation verification, retry loop,
prompt-injection defences, vision-mode grounding). The choice is
purely about transport ergonomics, not feature parity.

### `POST /extract` — the JSON-body path

**Request shape:**

```json
{
  "schema_name": "invoice",
  "schema_version": "1.0.0",
  "document_text": "INVOICE #INV-1234\nDate: 2026-04-15\n...",
  "document_id": "tenant-side-correlation-id"
}
```

| Field | Required | Notes |
|---|---|---|
| `schema_name` | yes | Must be registered. `GET /schemas` lists what's available. |
| `schema_version` | no | Pin-or-fail: when supplied, mismatch with the registered version returns 404. Omit to track HEAD. |
| `document_text` | yes | UTF-8, 1 to 200,000 characters. Anything outside that range is a 422 at the API layer (no Bedrock call burned). |
| `document_id` | no | Free-form string the caller carries through for correlation; ends up in OTel spans and logs. |

**Response shape (`200 OK`):**

```json
{
  "schema": "invoice",
  "schema_version": "1.0.0",
  "model_id": "us.anthropic.claude-sonnet-4-6-20250514-v1:0",
  "fields": [
    {
      "name": "invoice_number",
      "value": "INV-1234",
      "confidence": 0.97,
      "source_excerpt": "INVOICE #INV-1234"
    }
  ],
  "overall_confidence": 0.93,
  "warnings": [],
  "latency_ms": 812,
  "extracted_at": "2026-04-15T12:34:56Z",
  "correlation_id": "..."
}
```

Per-field `confidence` is the model's self-assessment, calibrated by
[ADR-0004](adr/0004-confidence-calibration.md): required-field weight,
validator-passed bonus, citation-verified bonus.
`overall_confidence` weights required fields more heavily and is
hard-zero when a required field is missing.

**Failure modes:**

| Status | Reason | Response body |
|---|---|---|
| 400 | Malformed JSON | FastAPI's default validation error |
| 401 | Auth failure (when `AUTH_MODE != none`) | `ErrorResponse{detail, correlation_id}` |
| 404 | Unknown `schema_name` or `schema_version` mismatch | Typed `ErrorResponse` |
| 422 | `document_text` too long, missing required fields, or other validation | FastAPI validation error array |
| 429 | Rate limit exceeded (when `RATE_LIMIT_ENABLED=true`) | slowapi default body + `Retry-After` header |
| 502 | Upstream Bedrock failure after retry exhaustion | Typed `ErrorResponse` |
| 500 | Unhandled internal error | `ErrorResponse{detail: "internal server error", correlation_id}` |

The `correlation_id` field threads through every response and every
log/span. Capture it client-side for debugging.

### `POST /extract/document` — the multipart upload path

Multipart form fields:

| Field | Required | Notes |
|---|---|---|
| `file` | yes | PDF (`application/pdf`) or image (`image/png`, `image/jpeg`, `image/webp`, `image/gif`). 5 MiB upload cap. |
| `schema_name` | yes | Same as `/extract`. |
| `schema_version` | no | Same pin-or-fail as `/extract`. |
| `document_id` | no | Same correlation hint. |

**Routing rules** (full design in [`extraction-modes.md`](extraction-modes.md)):

```
upload → MIME type check
  ├─ image/{png,jpeg,webp,gif}  → vision path (Bedrock multimodal)
  ├─ application/pdf
  │    └─ pypdf.extract_text() succeeds on any page?
  │           ├─ yes → text path  (same as /extract)
  │           └─ no  → rasterise to PNG via pypdfium2
  │                    (200 DPI, 5-page cap) → vision path
  └─ everything else            → 422 with a clear MIME message
```

**Response shape** is identical to `/extract` — same `ExtractionResult`
JSON across both endpoints. Vision-mode results have
`source_excerpt` populated from the model's quoted-from-image text;
the second-pass grounding verifier ([ADR-0011](adr/0011-prompt-injection-threat-model.md))
runs invisibly and may surface `UNGROUNDED:<field_name>` in the
`warnings[]` array if the verifier flagged a value across all retries.

**curl example:**

```bash
curl -s -X POST http://127.0.0.1:8000/extract/document \
  -F schema_name=invoice \
  -F file=@invoice.pdf \
  | jq
```

### `POST /extract/stream` — Server-Sent Events

Same JSON request body as `/extract`. Response is `text/event-stream`
with two or more frames:

```
event: chunk
data: {"text": "{\"fields\": ["}

event: chunk
data: {"text": "{\"name\": \"invoice_number\", \"value\": "}

…

event: result
data: {"schema":"invoice","fields":[…],"overall_confidence":0.93,…}
```

On parse/validation failure of the accumulated stream:

```
event: error
data: {"detail": "Could not parse JSON from streamed response: …"}
```

**Important streaming-specific behaviour** (see
[ADR-0014](adr/0014-streaming-retry-bypass.md)):

- The retry loop is **not** applied to streamed extractions. After a
  stream-side error, callers should fall back to `POST /extract` for
  the self-correcting retry pipeline.
- Per-field validation runs at end-of-stream only — clients see raw
  text deltas, not pre-validated fields. Partial-field streaming is a
  future enhancement.
- Streaming is text-mode only. Multipart upload (and the vision path)
  is not exposed over SSE.

**Reading the stream from Python:**

```python
import httpx, json
event_type: str | None = None
with httpx.stream("POST", "http://localhost:8000/extract/stream",
                 json={"schema_name": "invoice", "document_text": "..."}) as r:
    for line in r.iter_lines():
        if line.startswith("event:"):
            event_type = line.removeprefix("event: ").strip()
        elif line.startswith("data:") and event_type is not None:
            payload = json.loads(line.removeprefix("data: ").strip())
            print(event_type, payload)
```

### `POST /a2a/jsonrpc` — the A2A path

Opt-in via `A2A_ENABLED=true`. Discovery: `GET /.well-known/agent-card.json`.
Full design: [ADR-0012](adr/0012-a2a-protocol.md).

**Calling pattern (JSON-RPC 2.0 `message/send`):**

```json
{
  "jsonrpc": "2.0",
  "id": "<uuid4>",
  "method": "message/send",
  "params": {
    "message": {
      "role": "user",
      "parts": [
        {
          "kind": "data",
          "data": {
            "schema_name": "invoice",
            "document_text": "INVOICE #...",
            "schema_version": "1.0.0"
          }
        }
      ],
      "message_id": "<uuid4>"
    }
  }
}
```

The DataPart payload goes through the same `ExtractRequestBody`
Pydantic model as `/extract` (so `max_length=200_000`,
`extra="forbid"`, all type checks apply). The validated
`ExtractionResult` returns as a DataPart artifact named
`extraction_result`:

```json
{
  "jsonrpc": "2.0",
  "id": "<same uuid>",
  "result": {
    "id": "<task uuid>",
    "context_id": "<context uuid>",
    "status": {"state": "completed"},
    "artifacts": [
      {
        "name": "extraction_result",
        "parts": [
          {
            "kind": "data",
            "data": {"schema": "invoice", "fields": [...], ...}
          }
        ]
      }
    ]
  }
}
```

Vision over A2A is deliberately deferred. Send images via the HTTP
multipart endpoint instead.

---

## Schemas — discovery and registration

### Discover what's registered

```bash
curl -s http://localhost:8000/schemas | jq
```

Returns each schema's name, version, description, field count, and
required-field list. Two schemas ship by default:
[`invoice`](https://github.com/Grimm07/bedrock-strands-extraction-agent/blob/main/src/bedrock_strands_agent/extraction/schemas/examples.py)
and
[`irs_w9`](https://github.com/Grimm07/bedrock-strands-extraction-agent/blob/main/src/bedrock_strands_agent/extraction/schemas/examples.py).

### Register a new schema

Add to your bootstrap module (typically the same place you call
`create_app`):

```python
from bedrock_strands_agent.extraction.models import (
    FieldDefinition, FieldType, FormSchema,
)
from bedrock_strands_agent.extraction.schemas import register_schema

register_schema(
    FormSchema(
        name="purchase_order",
        version="1.0.0",
        description="Standard purchase order with line-item totals.",
        fields=(
            FieldDefinition(
                name="po_number", type=FieldType.STRING, required=True,
                description="Unique PO ID assigned by the buyer.",
                examples=("PO-12345",),
            ),
            FieldDefinition(
                name="signature_date", type=FieldType.DATE, required=False,
                description="Buyer signature date in ISO-8601 (YYYY-MM-DD).",
            ),
            # ...
        ),
    )
)
```

The Strands `add-form-schema` skill in `.claude/skills/` walks through
this end-to-end (also writes the test). Field types include `STRING`,
`INTEGER`, `NUMBER`, `BOOLEAN`, `DATE`, `EMAIL`, `SSN`, `EIN`,
`CURRENCY`. Date and SSN/EIN/email fields get free semantic
validation via the schema-shared `validate_value` helper.

### Pin to a schema version

When you've validated your downstream pipeline against `invoice@1.0.0`
and don't want a registry bump to silently change behaviour:

```bash
curl -X POST http://localhost:8000/extract \
  -d '{"schema_name":"invoice","schema_version":"1.0.0",...}'
```

Mismatch returns `404` with `detail: "Schema 'invoice' version '1.0.0' not registered (current registered version: '1.1.0')"`.

---

## Auth setup

Set `AUTH_MODE` to one of:

| Mode | Caller sends | Validation |
|---|---|---|
| `none` (default) | nothing | None — service assumes trusted upstream. Acceptable in dev or behind a sidecar/mesh. |
| `apikey` | `X-API-Key: <key>` header | Constant-time match against `API_KEYS` (CSV in env or Secrets Manager). |
| `jwt` | `Authorization: Bearer <token>` | Cognito JWKS validation (issuer + audience + signature). |
| `both` | either of the above | Either path counts as authorised. |

Always-public paths (the agent card and probes) bypass auth regardless:

```
/health      /metrics      /docs      /openapi.json      /redoc
/.well-known/agent-card.json (when A2A_ENABLED)
/a2a/.well-known/agent-card.json (when A2A_ENABLED)
```

Customise via `AUTH_EXCLUDED_PATHS` (CSV). The agent-card paths are
hard-excluded by AuthMiddleware regardless of override (A2A spec
requires unauthenticated discovery).

A 401 response body carries the `correlation_id` so client-side
debugging stays easy:

```json
{"detail": "invalid or missing API key", "correlation_id": "<uuid>"}
```

See [`auth-misconfig.md`](runbooks/auth-misconfig.md) for failure
triage.

---

## Observability — what you get for free

Every successful request emits:

- A **`correlation_id`** in the response body, response headers
  (`X-Request-ID`), every JSON log line, and every OTel span. Honour
  the inbound `X-Request-ID` header if you set one — the service
  propagates rather than overwriting.
- A **JSON log line** per request with method, path, status, latency,
  correlation_id, plus extraction-specific events (retry attempts,
  citation failures, grounding outcomes).
- An OTel span tree:
  ```
  http.server.request
    └── extraction.run        (or extraction.stream)
          ├── extraction.attempt        (× retries+1)
          └── extraction.vision_grounding   (vision path only)
  ```
  Span attributes carry only metadata (schema name + version,
  `bedrock.model_id`, latency, field/warning counts, document_id,
  correlation_id) — never the document or field values. The PII
  redaction filter ([`redaction.py`](https://github.com/Grimm07/bedrock-strands-extraction-agent/blob/main/src/bedrock_strands_agent/security/redaction.py))
  also masks SSN/EIN/email/phone/CC patterns on log records.

Prometheus metrics are exposed at `/metrics` (HTTP request count and
latency by route+status) — note that custom domain metrics
(`extraction_validator_passed_total`, etc.) are roadmap-deferred.

To ship spans to your collector, set
`OTEL_EXPORTER_OTLP_ENDPOINT=https://your-collector:4317`. Insecure
endpoint by default; flip `OTEL_EXPORTER_OTLP_INSECURE=false` when
mTLS is wired.

---

## Error handling — practical guide

The most common failure modes a caller will encounter:

| Symptom | Likely cause | Action |
|---|---|---|
| `404` `Schema 'X' not registered` | Schema-name typo or registry hasn't loaded the schema | `GET /schemas` to see what's actually there. |
| `404` `version 'Y' not registered (current 'Z')` | Pinned version drifted from registry | Update the pin or remove `schema_version` to track HEAD. |
| `422` `document_text` too long | Caller exceeded 200,000 chars | Pre-summarise client-side or split the document. |
| `422` `unsupported Content-Type` (on `/extract/document`) | Wrong MIME on the upload | Use `application/pdf` or `image/{png,jpeg,webp,gif}`. |
| `422` `PDF text extraction yielded ... exceeding the cap` | A multi-MB-of-text PDF | Split client-side; the PDF→text path mirrors the JSON-body cap. |
| `502` `extraction failed: ...validator_failures...` | Model output failed schema validation across all retries | Inspect the `warnings[]` array; some fields may still be present. |
| `event: error` mid-stream | Streaming-mode JSON parse failed at end-of-stream | Fall back to `POST /extract` (which has the retry loop). |
| `warnings[] = ["UNGROUNDED:vendor_name", ...]` | Vision verifier could not confirm the value across all retries; result still shipped | Treat as "human-review-needed" downstream. The result is shipped (per [ADR-0016](adr/0016-vision-grounding-second-pass.md)) so the caller has both the value and the warning to make their own routing decision. |

**Idempotency.** The service is stateless and side-effect-free; safe
to retry any failed request with the same body. There is no
client-supplied idempotency key today (no need — no writes happen
server-side).

**Retries.** Bedrock-side throttling/5xx is retried internally with
exponential-jitter backoff (3 attempts; see
[`bedrock_retry.py`](https://github.com/Grimm07/bedrock-strands-extraction-agent/blob/main/src/bedrock_strands_agent/agent/bedrock_retry.py)).
Schema-validation failures trigger up to 2 self-correcting model
re-prompts ([ADR-0009](adr/0009-bounded-self-correcting-retry.md)).
After all that, the caller sees a 502. Don't add aggressive client-
side retries on 502 — the service has already exhausted its budget.

---

## Cost model in 30 seconds

Bedrock invocations dominate operating cost by ~100× over compute /
networking. Per-request math (Claude Sonnet 4.6,
$3/M input + $15/M output):

| Path | Median per call | Worst case (retries × grounding) |
|---|---|---|
| `/extract` text (1-page) | ~$0.027 | ~$0.081 (3 attempts) |
| `/extract/document` vision (1-page) | ~$0.060 (extract + grounding) | ~$0.180 (3 × 2 calls) |
| `/extract/stream` | ~$0.027 (single attempt by design) | ~$0.027 |
| `/a2a/jsonrpc` | same as the underlying path | same |

Full breakdown with monthly traffic bands and cost-reduction levers:
[`cost-breakdown.md`](cost-breakdown.md). Headline lever: swap to
Claude Haiku 4.5 for schemas that don't need Sonnet's reasoning depth
— ~12× cheaper.

---

## Local development

**One terminal:**

```bash
make install                  # uv sync, pre-commit install
cp .env.example .env          # then edit AWS_REGION + creds (or use stub mode)
make dev                      # uvicorn at :8000 with auto-reload
```

**Stub mode (no Bedrock spend, deterministic responses):**

```bash
uv run python scripts/_boot_with_stub.py
# binds 127.0.0.1:8765 with a MagicMock-backed ExtractionService
```

The stub returns canned invoice JSON for every request — useful for
smoke tests, CI, or front-end development. The CI load-test workflow
([`load-test.yml`](https://github.com/Grimm07/bedrock-strands-extraction-agent/blob/main/.github/workflows/load-test.yml))
runs against this stub.

**Observability stack (Jaeger UI for local OTel):**

```bash
docker compose -f docker/docker-compose.yml up
# Service at :8000, Jaeger UI at :16686, OTel collector at :4317
```

**Quality gate before pushing:**

```bash
make check     # ruff + mypy + pytest (≥85% cov) + bandit
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the project's pre-commit
review rule and conventions.

---

## Production deployment checklist

The `infra/` directory is roadmap-deferred ([Critical #1](ROADMAP.md)),
so deployment is currently manual. Required env vars and IAM scopes
are tabulated in [`deployment-variables.md`](deployment-variables.md).

Minimum env-var set for a real deployment:

```bash
SERVICE_ENV=prod
AWS_REGION=us-east-1
BEDROCK_MODEL_ID=us.anthropic.claude-sonnet-4-6-20250514-v1:0
LOG_LEVEL=WARNING
LOG_FORMAT=json
AUTH_MODE=apikey
API_KEYS=...                                              # from Secrets Manager
RATE_LIMIT_ENABLED=true                                   # if internet-facing
OTEL_EXPORTER_OTLP_ENDPOINT=https://collector.example.com:4317
OTEL_EXPORTER_OTLP_INSECURE=false
A2A_ENABLED=true                                          # if other agents call you
A2A_PUBLIC_URL=https://extract.example.com/a2a/jsonrpc
```

**Required IAM scopes on the runtime task role:**

- `bedrock:InvokeModel` on the model ARNs (including cross-region
  inference profiles)
- `secretsmanager:GetSecretValue` on the API-key / JWT secrets
- `logs:CreateLogStream` / `logs:PutLogEvents` for CloudWatch
- `kms:Decrypt` on the customer-managed key (if Secrets Manager uses
  one)

**PagerDuty / Opsgenie wiring is deferred.** The runbooks describe
the paging policy; the SNS-topic + service-key Terraform variables
are listed as TODO in [`deployment-variables.md`](deployment-variables.md).

---

## Endpoint reference card

```
GET  /health                                # liveness probe
GET  /metrics                               # Prometheus metrics
GET  /schemas                               # registered schemas
POST /extract                               # text-mode extraction
POST /extract/document                      # multipart PDF / image
POST /extract/stream                        # SSE streaming
POST /a2a/jsonrpc                           # A2A protocol (opt-in)
GET  /.well-known/agent-card.json           # A2A discovery (opt-in)
GET  /a2a/.well-known/agent-card.json       # A2A discovery (namespaced)
GET  /docs           |  /redoc              # OpenAPI viewer
GET  /openapi.json                          # OpenAPI spec
```

---

## Where to go next

- Decided how to call this service? Skim [ADR-0011](adr/0011-prompt-injection-threat-model.md)
  for the threat model — it explains why the service requires
  structured payloads and what defences run between input and model.
- Building tooling around it? Check
  [`tech-stack.md`](tech-stack.md) for everything in the
  dependency tree and why.
- Operating it? Read the runbooks in
  [`runbooks/`](runbooks/oncall.md) and the SLO doc
  [`slos.md`](slos.md).
- Integrating over A2A? The full agent-card shape and skill list is
  generated dynamically — `GET /.well-known/agent-card.json` is the
  authoritative document. Design rationale in
  [ADR-0012](adr/0012-a2a-protocol.md).
