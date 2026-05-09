# Load test (k6)

This directory ships a [k6](https://k6.io/) script that exercises the
`/extract` endpoint with two scenarios in sequence:

| Scenario | Rate     | Duration | Purpose                                         |
| -------- | -------- | -------- | ----------------------------------------------- |
| `smoke`  | 5 RPS    | 2 min    | Cheap regression check on every release.        |
| `soak`   | 50 RPS   | 5 min    | Validates p95 < 3 s and error rate < 1% (SLOs). |

Thresholds match `docs/slos.md`. CI does **not** run this by default — it
costs Bedrock invocations against staging. The make target is for manual /
release-time runs.

## Local (against `make dev`)

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
