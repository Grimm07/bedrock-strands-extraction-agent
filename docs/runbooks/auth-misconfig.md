# Runbook: 401 spike / auth misconfiguration

## Symptoms

- 401 spike from `/extract` in CloudWatch (`http_requests_total{handler="/extract",
  status="401"}`).
- Log lines from `bedrock_strands_agent.api.auth`:
  - `JWT rejected: <reason>` — token is bad, JWKS fetch failed, or audience/issuer
    mismatch.
  - `invalid or missing API key`, `invalid or missing JWT`, or
    `invalid or missing credentials`.

## Quick triage (≤ 3 commands)

```bash
# 1. Are 401s concentrated on one mode or one path?
aws logs start-query --log-group-name /aws/bedrock-agentcore/bedrock-strands-agent/<env> \
  --query-string 'fields detail, count(*) | filter status_code = 401 | stats count(*) by detail' \
  --start-time $(date -u -v-15M +%s)

# 2. Inspect current AUTH_MODE and excluded-path config:
aws ecs describe-task-definition --task-definition bedrock-strands-agent-<env> | \
  jq '.taskDefinition.containerDefinitions[].environment[] | select(.name|startswith("AUTH_") or startswith("JWT_"))'

# 3. Is the JWKS endpoint reachable from the Runtime?
aws cognito-idp describe-user-pool --user-pool-id <pool-id> --query 'UserPool.Domain'
curl -fsS "https://cognito-idp.<region>.amazonaws.com/<pool-id>/.well-known/jwks.json" | jq '.keys[].kid'
```

## Diagnosis

| Detail string in 401 body              | Likely cause                                                                                                            |
| -------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `invalid or missing API key`           | `AUTH_MODE=apikey` but client is using a stale/rotated key; or the Secrets Manager rotation Lambda dropped a key.       |
| `invalid or missing JWT`               | Cognito client misconfig: missing `client_credentials` flow, wrong audience, or scope `extract` not granted.            |
| `invalid or missing credentials`       | `AUTH_MODE=both` and neither header arrived. Usually a calling-team header-name typo (e.g. `X-Api-Key` vs `X-API-Key`). |
| `JWT rejected: Invalid audience`       | Resource server identifier in Cognito doesn't match `JWT_AUDIENCE`. Update one or the other.                            |
| `JWT rejected: Signature verification failed` | The client is talking to the wrong pool, or the key has rotated and our 6 h JWKS cache hasn't refreshed yet.     |
| `JWT rejected: ...expired...`          | Client's clock skew or it isn't refreshing tokens. Not our problem.                                                     |

Cross-reference: when `RUNTIME_MODE=agentcore`, the AgentCore Runtime's
`custom_jwt_authorizer` validates JWTs *before* our middleware sees the
request. So a JWT failure logged by us means the AgentCore authorizer
already let it through — focus on Cognito audience/issuer first.

## Mitigation

1. **Stale API key** — rotate via Secrets Manager; new value lands in
   `app.state.settings.api_keys` on next Runtime start. Force a restart by
   pushing a no-op image tag.
2. **JWKS rotation lag** — restart the Runtime to invalidate the in-process
   JWKS cache. The cache TTL is 6 h.
3. **Cognito misconfig** — check `aws_cognito_resource_server` identifier
   matches `JWT_AUDIENCE`; fix in `infra/tofu/modules/agentcore-identity/`
   and re-apply.
4. **Wrong header name** — the calling team is at fault; share the
   `auth_excluded_paths` list for `/health` / `/metrics` so they can verify
   their request shape outside the auth path first.

## Escalation

If a Cognito user pool is corrupt or unrecoverable, the rebuild cost is
non-trivial (clients must re-issue secrets). Loop in the
`agentcore-identity` module owner before nuking.
