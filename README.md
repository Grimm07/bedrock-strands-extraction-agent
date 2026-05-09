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
- **MCP-ready** — declarative `mcp.config.json`, one env var to enable servers,
  their tools surface to the agent at startup.
- **Self-correcting retry loop** — on validation failure the service re-prompts
  once with the specific errors before giving up.
- **Production HTTP layer** — FastAPI with structured JSON logs, X-Request-ID
  correlation, Prometheus `/metrics`, and OpenTelemetry tracing
  (FastAPI + botocore instrumentors).
- **Quality gate baked in** — ruff, mypy strict, pytest with ≥80% coverage,
  bandit, pip-audit, Trivy, CodeQL.

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
curl -X POST http://localhost:8000/extract \
  -H 'Content-Type: application/json' \
  -d '{"schema_name": "invoice", "document_text": "INVOICE #INV-1234\nDate: 2026-04-15\n..."}'
```

## HTTP API

| Method | Path        | Description                                        |
| ------ | ----------- | -------------------------------------------------- |
| GET    | `/health`   | Liveness probe; returns service name and version.  |
| GET    | `/schemas`  | List schemas in the registry.                      |
| POST   | `/extract`  | Run extraction. Body: `ExtractionRequest`.         |
| GET    | `/metrics`  | Prometheus metrics (text/plain; not in OpenAPI).   |

`X-Request-ID` is honoured on the way in and echoed on the way out. If you
do not send one, the server generates a UUID and uses it as the
`extraction.correlation_id` span attribute.

### `POST /extract` request

```json
{
  "schema_name": "invoice",
  "document_text": "<the OCR'd or extracted text of the document>",
  "document_id": "optional-tenant-side-id"
}
```

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
make check     # ruff + mypy + pytest (≥80% cov) + bandit
make audit     # pip-audit against exported lockfile
make ci        # install + check + audit
```

## License

[Apache-2.0](LICENSE).
