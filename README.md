# bedrock-strands-agent

A production-ready form-extraction agent built on the
[Strands Agents SDK](https://github.com/strands-agents/sdk-python) with
[Amazon Bedrock](https://aws.amazon.com/bedrock/) as the model provider.
It reads document text and returns structured field values defined by a
schema (IRS W-9, invoices, or any registered schema you add).

## Features

- **Strands + Bedrock** — Claude Sonnet 4.6 by default, fully overridable.
- **Schema-driven extraction** — register a `FormSchema`, the service does the
  rest: prompt rendering, tool exposure, JSON parsing, validation, retry.
- **Three input paths**:
  - `POST /extract` — pre-extracted text in the request body.
  - `POST /extract/document` — multipart upload. PDFs with embedded text
    auto-route to the text path; PDFs without text are rasterised
    server-side (pypdfium2, 200 DPI, 5-page cap) and routed to vision;
    images (PNG/JPEG/WebP/GIF) go straight to Bedrock multimodal vision.
  - `POST /extract/stream` — Server-Sent Events. `event: chunk` per text
    delta, then a terminal `event: result` (or `event: error`).
- **Schema versioning** — request bodies and the multipart form both accept
  an optional `schema_version` to pin against a specific registered version
  (404 on mismatch).
- **MCP-ready** — declarative `mcp.config.json`, one env var to enable servers,
  their tools surface to the agent at startup.
- **Self-correcting retry loop** — on validation failure the service
  re-prompts up to twice (configurable; see ADR-0009) with the specific
  errors before giving up. Streaming intentionally bypasses the retry loop.
- **Production HTTP layer** — FastAPI with auth (`apikey | jwt | both | none`),
  slowapi rate limiting, structured JSON logs, X-Request-ID correlation,
  Prometheus `/metrics`, and OpenTelemetry tracing (FastAPI + botocore
  instrumentors). Routes offload sync work via `asyncio.to_thread` so the
  event loop never blocks on Bedrock.
- **Quality gate baked in** — ruff, mypy strict, pytest with ≥85% coverage,
  bandit, pip-audit, Trivy, CodeQL, semgrep with project-specific rules
  including `no-print-of-document-text` (PII guard).

## Quick start

```bash
# Install Python and dependencies
make install

# Configure
cp .env.example .env
# (edit .env if you want non-default Bedrock or OTEL settings)

# Run the dev server
make dev

# In another shell
curl http://localhost:8000/health
curl http://localhost:8000/schemas

# Text extraction
curl -X POST http://localhost:8000/extract \
  -H 'Content-Type: application/json' \
  -d '{"schema_name": "invoice", "document_text": "INVOICE #INV-1234\nDate: 2026-04-15\n..."}'

# Document upload (PDF or image; auto-routes to text or vision path)
curl -X POST http://localhost:8000/extract/document \
  -F schema_name=invoice \
  -F file=@invoice.pdf

# Streaming (Server-Sent Events; event: chunk frames + terminal result)
curl -X POST http://localhost:8000/extract/stream \
  -H 'Content-Type: application/json' \
  -d '{"schema_name": "invoice", "document_text": "..."}' \
  --no-buffer
```

## HTTP API

| Method | Path                | Description                                                            |
| ------ | ------------------- | ---------------------------------------------------------------------- |
| GET    | `/health`           | Liveness probe; returns service name and version.                      |
| GET    | `/schemas`          | List schemas in the registry.                                          |
| POST   | `/extract`          | Run extraction on pre-extracted text. Body: `ExtractRequestBody`.      |
| POST   | `/extract/document` | Multipart upload (PDF or image). Auto-routes to text or vision.        |
| POST   | `/extract/stream`   | Stream a text-mode extraction as Server-Sent Events.                   |
| GET    | `/metrics`          | Prometheus metrics (text/plain; not in OpenAPI).                       |

When `A2A_ENABLED=true`, the service additionally mounts:

| Method | Path                                  | Description                                                         |
| ------ | ------------------------------------- | ------------------------------------------------------------------- |
| GET    | `/.well-known/agent-card.json`        | A2A discovery document (canonical path; unauthenticated).           |
| GET    | `/a2a/.well-known/agent-card.json`    | Namespaced copy of the agent card.                                  |
| POST   | `/a2a/jsonrpc`                        | A2A JSON-RPC 2.0 endpoint. `message/send`, `tasks/get`, etc.        |

A2A messages must carry a `DataPart` payload with `schema_name` +
`document_text`; the validated `ExtractionResult` returns as a
`DataPart` artifact. See [ADR-0012](docs/adr/0012-a2a-protocol.md) for
the design and `docs/deployment-variables.md` for the env vars.

`X-Request-ID` is honoured on the way in and echoed on the way out. If you
do not send one, the server generates a UUID and uses it as the
`extraction.correlation_id` span attribute.

### `POST /extract` request

```json
{
  "schema_name": "invoice",
  "schema_version": "1.0.0",
  "document_text": "<the OCR'd or extracted text of the document>",
  "document_id": "optional-tenant-side-id"
}
```

`schema_version` is optional. When supplied, the request fails with `404` if
the registry's current version for `schema_name` does not match (pin-or-fail).
Omit to track HEAD of the registry. The same field is also accepted as a
`Form` field on `POST /extract/document`.

### `POST /extract/document` request

Multipart form with one file plus a few text fields:

| Field | Required | Description                                                                |
| --- | --- | --- |
| `file` | yes | PDF (`application/pdf`) or image (`image/png`, `image/jpeg`, `image/webp`, `image/gif`). 5 MiB upload cap. |
| `schema_name` | yes | Same as `/extract`. |
| `schema_version` | no | Same pin-or-fail semantics as above. |
| `document_id` | no | Tenant-side correlation id. |

Routing rules (full design in [`docs/extraction-modes.md`](docs/extraction-modes.md)):

- PDF with embedded text → text path (free; deterministic; same response as
  `/extract`).
- Image → Bedrock multimodal vision via `Converse`.
- Scanned PDF (no embedded text) → server-side rasterised to PNG via
  `pypdfium2` at 200 DPI, capped at 5 pages, then routed to vision.

### `POST /extract/stream` response

Streams `text/event-stream` with one frame per text delta and exactly one
terminal frame:

```
event: chunk
data: {"text": "{\"fields\": ["}

event: chunk
data: {"text": "{\"name\": \"invoice_number\", "}

…

event: result
data: {"schema": "invoice", "fields": [...], "overall_confidence": 0.93, ...}
```

On a parse/validation failure of the accumulated stream:

```
event: error
data: {"detail": "Could not parse JSON from streamed response: …"}
```

The retry loop is **not** applied to streamed extractions; callers fall
back to `POST /extract` after a stream-side error.

### `POST /extract` response

```json
{
  "schema": "invoice",
  "schema_version": "1.0.0",
  "model_id": "us.anthropic.claude-sonnet-4-6-20250514-v1:0",
  "fields": [
    { "name": "invoice_number", "value": "INV-1234", "confidence": 0.97, "source_excerpt": "INVOICE #INV-1234" }
  ],
  "overall_confidence": 0.93,
  "warnings": [],
  "latency_ms": 812,
  "extracted_at": "2026-05-08T12:34:56.000Z",
  "correlation_id": "..."
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  HTTP request                                                        │
└──────────┬──────────────────────────────────────────────────────────┘
           ▼
┌──────────────────────┐    ┌──────────────────────┐
│ CorrelationIdMiddle. │───▶│  /health  /schemas   │
│ (X-Request-ID)       │    │  /metrics  /extract  │
└──────────────────────┘    └──────────┬───────────┘
                                       ▼
                          ┌────────────────────────┐
                          │  ExtractionService     │
                          │  ─ render prompt       │
                          │  ─ invoke agent        │
                          │  ─ parse JSON          │
                          │  ─ coerce + validate   │
                          │  ─ retry once if bad   │
                          └────────────┬───────────┘
                                       ▼
              ┌──────────────────────────────────────────┐
              │  Strands Agent  (BedrockModel)           │
              │   ├─ DEFAULT_TOOLS (validators, dates)   │
              │   └─ MCP tools  (filesystem, fetch, …)   │
              └──────────────────────────────────────────┘
                                       ▼
                              Amazon Bedrock
                              (Claude Sonnet 4.6)
```

## Configuration

Every field is settable via the matching upper-snake-case env var.

| Setting                              | Default                                              | Notes                                      |
| ------------------------------------ | ---------------------------------------------------- | ------------------------------------------ |
| `service_name`                       | `bedrock-strands-agent`                              | Used for logs and OTel `service.name`.     |
| `service_env`                        | `local`                                              | `local`, `dev`, `staging`, `prod`.         |
| `log_level`                          | `INFO`                                               | `DEBUG` / `INFO` / `WARNING` / `ERROR`.    |
| `log_format`                         | `json`                                               | `json` or `text`.                          |
| `api_host`                           | `0.0.0.0`                                            | Bind address (intentional inside a pod).   |
| `api_port`                           | `8000`                                               |                                            |
| `aws_region` (alias `AWS_REGION`)    | `us-east-1`                                          | Bedrock region.                            |
| `bedrock_model_id`                   | `us.anthropic.claude-sonnet-4-6-20250514-v1:0`       |                                            |
| `bedrock_max_tokens`                 | `4096`                                               |                                            |
| `bedrock_temperature`                | `0.0`                                                | 0.0–1.0.                                   |
| `bedrock_top_p`                      | `0.9`                                                | 0.0–1.0.                                   |
| `otel_exporter_otlp_endpoint`        | (unset)                                              | If set, ships spans via OTLP/gRPC.         |
| `otel_exporter_otlp_insecure`        | `true`                                               |                                            |
| `otel_service_name`                  | (unset; falls back to `service_name`)                |                                            |
| `strands_otel_enable_console_export` | `false`                                              | Console exporter when no OTLP endpoint.    |
| `mcp_enabled_servers`                | `[]`                                                 | CSV of server names from `mcp.config.json`.|
| `mcp_config_path`                    | `mcp.config.json`                                    | Path is resolved against CWD.              |

## Adding a schema

```python
from bedrock_strands_agent.extraction.models import FieldDefinition, FieldType, FormSchema
from bedrock_strands_agent.extraction.schemas import register_schema

register_schema(
    FormSchema(
        name="purchase_order",
        version="1.0.0",
        description="Standard purchase order with line items.",
        fields=(
            FieldDefinition(name="po_number", type=FieldType.STRING, required=True, description="Unique PO ID"),
            # …
        ),
    )
)
```

Call `register_schema` at application startup (e.g. in your own bootstrap
module imported before `create_app`).

## Adding an MCP server

Add an entry to `mcp.config.json` (stdio-only for now):

```json
{
  "mcpServers": {
    "my-server": {
      "transport": "stdio",
      "command": "uvx",
      "args": ["my-mcp-package"],
      "env": {}
    }
  }
}
```

Then enable it: `MCP_ENABLED_SERVERS=my-server`. The server is launched at app
startup; its tools are concatenated onto `DEFAULT_TOOLS`.

## Container

```bash
make docker-build
docker run --rm -p 8000:8000 \
  -e AWS_REGION=us-east-1 \
  -e AWS_ACCESS_KEY_ID=... \
  -e AWS_SECRET_ACCESS_KEY=... \
  bedrock-strands-agent:dev
```

For local observability:

```bash
docker compose -f docker/docker-compose.yml up
# Jaeger UI: http://localhost:16686
```

## CLI

The CLI exposes two subcommands:

```bash
# Serve HTTP
uv run python -m bedrock_strands_agent serve

# One-shot extract from stdin
cat invoice.txt | uv run python -m bedrock_strands_agent extract --schema invoice --file -
```

## Quality gate

```bash
make check     # ruff + mypy + pytest (≥85% cov) + bandit
make audit     # pip-audit against exported lockfile
make ci        # install + check + audit
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for setup, testing, and review
expectations.

## License

[Apache-2.0](LICENSE).
