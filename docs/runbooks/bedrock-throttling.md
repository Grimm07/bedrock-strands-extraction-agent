# Runbook: Bedrock throttling / retry-rate elevated

## Symptoms

- CloudWatch alarm: `BedrockStrandsAgent-{env}-bedrock-throttles` (`AWS/Bedrock`
  metric `InvocationThrottles` > 0 over 1 min) or
  `BedrockStrandsAgent-{env}-extraction-retry-rate-high`.
- X-Ray traces showing `bedrock.retry` events on the `extraction.run` span
  (added by `agent/bedrock_retry.py`).
- Log lines `Retrying Bedrock call (attempt N)` from the
  `bedrock_strands_agent.bedrock_retry` logger.

## Quick triage (≤ 3 commands)

```bash
# 1. How many throttles in the last 30 min?
aws cloudwatch get-metric-statistics --namespace AWS/Bedrock \
  --metric-name InvocationThrottles \
  --dimensions Name=ModelId,Value=<BEDROCK_MODEL_ID> \
  --start-time $(date -u -v-30M +%FT%TZ) --end-time $(date -u +%FT%TZ) \
  --period 60 --statistics Sum

# 2. Which tenants are paying the cost?
aws logs start-query --log-group-name /aws/bedrock-agentcore/bedrock-strands-agent/<env> \
  --query-string 'fields tenant.id, count(*) | filter ispresent(`bedrock.retry`) | stats count(*) by `tenant.id`' \
  --start-time $(date -u -v-30M +%s)

# 3. Is the model the bottleneck or the account quota?
aws service-quotas get-service-quota --service-code bedrock \
  --quota-code <see AWS console for InvokeModel quota>
```

## Diagnosis

Look at the `extraction.run` span attributes (X-Ray):

| Attribute                  | What it tells you                                                  |
| -------------------------- | ------------------------------------------------------------------ |
| `bedrock.model_id`         | Which model is throttling.                                         |
| `extraction.retry_attempt` | How deep the retry loop went on the failing request.               |
| `extraction.schema`        | Whether one schema (e.g. high-token W-9) dominates.                |
| `bedrock.input_tokens`     | (Phase D) Per-request token cost — flag if anomalously large.      |

If only one schema or one tenant dominates, we have a content problem; if all
spans are throttling, the account quota is the limit.

## Mitigation

1. **Account quota** — file a quota-increase ticket via AWS Support for the
   `Cross-region inference for {model}` quota. Mitigation while waiting:
   - Reduce `RATE_LIMIT_PER_MINUTE` env var in
     `infra/tofu/envs/{env}/terraform.tfvars` and re-apply, so we shed load
     before Bedrock does.
2. **Single-tenant abuse** — the default key-aware `slowapi` limit kicks in
   per-API-key. If a client is staying just under the per-key limit but
   summed traffic blows the quota, drop the per-key limit on that tenant
   until they behave.
3. **Single schema dominating** — consider switching that schema to Haiku
   (`BEDROCK_MODEL_ID` override per-deployment); accept slight accuracy
   loss for throughput.
4. **Persistent** — the retry wrapper masks the throttling cost from
   callers up to 3 attempts at exponential-jitter backoff (initial 0.5 s,
   max 10 s). If retry rate > 10% sustained, escalate to SEV-2.

## Escalation

If the quota increase is denied or expected delivery is > 1 business day,
notify product/CS and consider routing the highest-volume tenants to a
dedicated AgentCore Runtime in a different region.
