# Runbook: /extract 502 spike

## Symptoms

- CloudWatch alarm: `BedrockStrandsAgent-{env}-extraction-error-rate-high`
  (`http_requests_total{handler="/extract", status="502"}` rate > 1% over
  5 min).
- Log marker: `extraction failed` from `bedrock_strands_agent.api.routes`
  (raised at `routes.py` after an `ExtractionError`).
- `extraction.warning_count` span attribute non-zero on most recent traces.

## Quick triage (≤ 3 commands)

```bash
# 1. Are we looking at one schema or all of them?
aws logs start-query --log-group-name /aws/bedrock-agentcore/bedrock-strands-agent/<env> \
  --query-string 'fields `extraction.schema`, count(*) | filter @message like /extraction failed/ | stats count(*) by `extraction.schema`' \
  --start-time $(date -u -v-15M +%s)

# 2. Sample one failing correlation_id end-to-end:
aws logs filter-log-events --log-group-name /aws/bedrock-agentcore/bedrock-strands-agent/<env> \
  --filter-pattern 'extraction failed' --max-items 1

# 3. Pull the X-Ray segment for that correlation_id and read its attributes:
aws xray batch-get-traces --trace-ids <id-from-step-2>
```

## Diagnosis

The exception path is `_invoke` → JSON parse → `_coerce_fields` →
`ExtractionError` → 502. The interesting `warnings` strings on the span:

| Warning prefix                                | What it usually means                                              |
| --------------------------------------------- | ------------------------------------------------------------------ |
| `MISSING_REQUIRED:<field>`                    | Model dropped a required field; the retry attempt also dropped it. |
| `pattern mismatch for <field>`                | Model emitted a value that fails the regex (e.g. malformed SSN).   |
| `Could not parse JSON from response`          | Model returned prose / fenced output our parser couldn't recover.  |
| `MISSING_REQUIRED` after retry exhausted      | Document genuinely lacks the field, schema is wrong, or model is.  |

Check `extraction.retry_attempt` — if every failure is `attempt=2` (i.e.
self-correcting retry didn't fix it), the schema or the prompt template is
the problem, not transient model variance.

## Mitigation

1. **Single-schema spike** — recent schema change, prompt-template edit, or
   few-shot example regression. Look at the latest commit touching
   `src/bedrock_strands_agent/extraction/schemas/examples.py` or
   `templates/extract.j2`. Roll back via image-tag swap (see
   `oncall.md` rollback section).
2. **All-schema spike** — usually correlated with a Bedrock model change
   (silent retirement, version flip). Confirm `BEDROCK_MODEL_ID` against the
   deployed Tofu var and the AWS Bedrock model availability page.
3. **Specific tenant** — their docs may have unusual structure. Ask them for
   a sample; reproduce locally with `pytest tests/test_bedrock_integration.py
   -v RUN_INTEGRATION_TESTS=1` after dropping the doc into
   `tests/fixtures/`.
4. **Persistent** — if no single cause, lower the `extraction-error-rate-high`
   alarm to ticket-only and gate the next deploy on a custom-pytest eval run
   (D5) catching the regression.

## Escalation

If error rate > 25% for > 30 min and rollback hasn't recovered it, treat as
SEV-1 — page the model owner / data-team contact and consider failing the
gateway over to a stale-but-working previous AgentCore Runtime version.
