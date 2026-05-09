# Load test (k6)

This directory ships a [k6](https://k6.io/) script that exercises the
`/extract` endpoint with three selectable scenarios:

| Scenario | Rate   | Duration | Purpose                                         |
| -------- | ------ | -------- | ----------------------------------------------- |
| `ci`     | 10 RPS | 30 s     | CI gate against the stub-mode app (offline). |
| `smoke`  | 5 RPS  | 2 min    | Cheap regression check on every release.       |
| `soak`   | 50 RPS | 5 min    | Validates p95 < 3 s and error rate < 1% (SLOs). |

Thresholds match `docs/slos.md` (p95 < 3 000 ms, error rate < 1%). Pick
which scenarios run via `K6_PROFILE`:

| `K6_PROFILE` | Scenarios | Where it runs |
|---|---|---|
| `ci` | `ci` only | GitHub Actions `Load test (k6 smoke)` workflow on every PR / push to main, against the stub-mode app (no Bedrock cost). |
| `smoke` | `smoke` only | Manual, against `make dev`. |
| `soak` | `soak` only | Manual / release-time, against staging. |
| `full` (default) | `smoke` + `soak` | Manual release gate. |

## CI (against `scripts/_boot_with_stub.py`)

The `Load test (k6 smoke)` workflow boots the FastAPI app with a
MagicMock-backed `ExtractionService` (no Bedrock calls), waits for
`/health`, and runs the `ci` scenario. Catches regressions in HTTP
routing, middleware, the asyncio event-loop layer, and the response
shape — without paying for Bedrock. See
`.github/workflows/load-test.yml`.

Reproduce locally:

```bash
uv run python scripts/_boot_with_stub.py &       # boots on 127.0.0.1:8765
BASE_URL=http://127.0.0.1:8765 K6_PROFILE=ci \
  k6 run tests/load/extract.js
```

## Local (against `make dev`, real Bedrock)

```bash
make dev          # one terminal — starts the FastAPI app
make load-test    # another terminal — runs the k6 script in Docker
```

## Against staging AgentCore Gateway

```bash
BASE_URL=https://<gateway-id>.gateway.bedrock-agentcore.us-east-1.amazonaws.com \
API_KEY=$(aws secretsmanager get-secret-value \
            --secret-id bedrock-strands-agent/staging/api-keys \
            --query SecretString --output text | jq -r '.k1') \
docker run --rm -i \
  -e BASE_URL -e API_KEY \
  -v $(pwd)/tests/load:/scripts \
  grafana/k6 run /scripts/extract.js
```

If the gateway requires a Cognito Bearer token instead of an API key, swap
`API_KEY` for `Authorization: Bearer …` in the script's `headers` block.

## Adding a payload

Append a JSON object to `payloads/sample_form.json`:

```json
{
  "schema_name": "<registered_schema>",
  "document_text": "...",
  "document_id": "k6-...-NNN"
}
```

The script round-robins through the list, so payloads should exercise the
common path (not edge-case validation failures).

## Interpreting the output

k6 prints per-metric summaries at the end. The two we gate on:

- `http_req_duration { p(95) }` — fail if ≥ 3 000 ms.
- `http_req_failed { rate }` — fail if ≥ 1%.

If either threshold trips, k6 exits non-zero. Investigate via the matching
runbook in `docs/runbooks/`.
