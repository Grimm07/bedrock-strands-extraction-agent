# Deployment variables checklist

Everything an operator must provide to stand the service up in a real
environment. Split into three groups:

1. **Application env vars** — read by `pydantic-settings` at boot. Most
   have defaults in `.env.example`; the ones marked **REQUIRED** must
   be set per environment.
2. **AWS-side identity / IAM** — what the runtime needs from AWS.
3. **Infrastructure-as-code variables** — Terraform / OpenTofu inputs
   when `infra/` lands (Roadmap Critical #1; not yet implemented).

Each section flags `dev` vs `staging`/`prod` differences explicitly.

---

## 1. Application env vars

Set via `.env` (gitignored, see `.env.example`), container env, or
ECS task definition. Source-of-truth:
`src/bedrock_strands_agent/config.py` (Pydantic `Settings` model).

### Service identity

| Var | Type | Default | Notes |
|---|---|---|---|
| `SERVICE_NAME` | str | `bedrock-strands-agent` | OTel `service.name`. |
| `SERVICE_ENV` | enum | `local` | `local` \| `dev` \| `staging` \| `prod`. **REQUIRED** for non-local. |

### HTTP

| Var | Type | Default | Notes |
|---|---|---|---|
| `API_HOST` | str | `0.0.0.0` | Bind address; intentional 0.0.0.0 inside containers. |
| `API_PORT` | int | `8000` | Match the Dockerfile `EXPOSE`. |

### Logging

| Var | Type | Default | Notes |
|---|---|---|---|
| `LOG_LEVEL` | enum | `INFO` | Set to `WARNING` in prod to keep CloudWatch ingest down. **Never set `DEBUG` in prod** — would route per-request data through the redaction filter unnecessarily and inflate cost. |
| `LOG_FORMAT` | enum | `json` | Production must be `json` for CloudWatch parsing. `text` is for local dev only. |

### Bedrock

| Var | Type | Default | Notes |
|---|---|---|---|
| `AWS_REGION` (alias of `aws_region`) | str | `us-east-1` | **REQUIRED** if running outside an environment where the boto3 default chain provides one. |
| `BEDROCK_MODEL_ID` | str | `us.anthropic.claude-sonnet-4-6-20250514-v1:0` | Cross-region inference profile. Override per-deployment — e.g. `us.anthropic.claude-haiku-4-5-20251001-v1:0` for cost-sensitive workloads. See `docs/cost-breakdown.md` cost levers. |
| `BEDROCK_MAX_TOKENS` | int | `4096` | Output cap. Range `[1, 200_000]`. Tune up only if a schema has many fields. |
| `BEDROCK_TEMPERATURE` | float | `0.0` | Determinism is the goal — leave at 0 unless you know why. |
| `BEDROCK_TOP_P` | float | `0.9` | Same. |

### Auth

| Var | Type | Default | Notes |
|---|---|---|---|
| `AUTH_MODE` | enum | `none` | `none` \| `apikey` \| `jwt` \| `both`. **`none` is acceptable in dev only.** Production behind a trusted internal caller may stay `none` if the upstream service handles auth; otherwise set to `apikey` or `jwt` (or both). |
| `API_KEYS` | CSV | empty | **REQUIRED** if `AUTH_MODE` is `apikey` or `both`. Keys must be at least 16 chars. Rotate via Secrets Manager. |
| `JWT_ISSUER` | URL | empty | **REQUIRED** if `AUTH_MODE` is `jwt` or `both`. e.g. `https://cognito-idp.us-east-1.amazonaws.com/us-east-1_XXXXXXXXX`. |
| `JWT_AUDIENCE` | str | empty | **REQUIRED** if `AUTH_MODE` is `jwt` or `both`. Cognito app-client id. |
| `JWT_JWKS_URL` | URL | empty | **REQUIRED** if `AUTH_MODE` is `jwt` or `both`. Typically `<issuer>/.well-known/jwks.json`. |
| `AUTH_EXCLUDED_PATHS` | CSV | `/health,/metrics,/docs,/openapi.json,/redoc` | Paths that bypass auth (probes, docs). |

### Rate limiting

| Var | Type | Default | Notes |
|---|---|---|---|
| `RATE_LIMIT_ENABLED` | bool | `false` | Set `true` in prod if the upstream caller is the open internet. Off-by-default because the threat model assumes a trusted upstream. |
| `RATE_LIMIT_PER_MINUTE` | int | `60` | Per-key (or per-IP fallback) request budget. |
| `RATE_LIMIT_BURST` | int | `10` | Burst allowance on top of the steady rate. |

### A2A (agent-to-agent) protocol

| Var | Type | Default | Notes |
|---|---|---|---|
| `A2A_ENABLED` | bool | `false` | Mount the A2A discovery + JSON-RPC endpoints. Off by default; opt in per environment. See [ADR-0012](adr/0012-a2a-protocol.md). |
| `A2A_PUBLIC_URL` | URL | empty | The URL advertised in `/.well-known/agent-card.json`. **REQUIRED** in staging/prod (other agents need a reachable URL). Empty falls back to `http://{API_HOST}:{API_PORT}/a2a/jsonrpc` — fine for local dev only. |

### MCP

| Var | Type | Default | Notes |
|---|---|---|---|
| `MCP_ENABLED_SERVERS` | CSV | empty | Names from `mcp.config.json` to start. Empty = no MCP servers. **Add tools cautiously** — every enabled server is a subprocess running with the worker's identity. |
| `MCP_CONFIG_PATH` | path | `mcp.config.json` | Resolved against CWD. |

### Telemetry

| Var | Type | Default | Notes |
|---|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | URL | empty | OTLP/gRPC endpoint. Empty disables OTLP. **Set in staging/prod** to ship traces to your collector. |
| `OTEL_EXPORTER_OTLP_INSECURE` | bool | `true` | TLS to the collector. Flip to `false` once you've wired mTLS. |
| `OTEL_SERVICE_NAME` | str | empty | Falls back to `SERVICE_NAME` if empty. |
| `STRANDS_OTEL_ENABLE_CONSOLE_EXPORT` | bool | `false` | **MUST stay `false` in prod** — the console exporter prints span attributes to stdout, which the security-reviewer agent treats as a leak risk. |

---

## 2. AWS-side identity / IAM

The runtime task / instance must have an IAM role with these
permissions. Scope each to the resource ARNs you actually use.

### Required for the service to function

| Action | Resource | Why |
|---|---|---|
| `bedrock:InvokeModel` | `arn:aws:bedrock:<region>::foundation-model/<model-id>` AND the cross-region inference variants (`arn:aws:bedrock:us-*::foundation-model/<model-id>`) | Every `/extract*` request. |
| `secretsmanager:GetSecretValue` | The ARNs of API-key and JWT-signing secrets (if used) | Auth bootstrap. |
| `kms:Decrypt` | The CMK that encrypts the secrets | Secrets Manager will need this if you use customer-managed encryption. |
| `logs:CreateLogStream`, `logs:PutLogEvents` | The CloudWatch Log group for this service | JSON log shipping. |

### Required if using `AUTH_MODE=jwt`

| Action | Resource | Why |
|---|---|---|
| Public network egress to the JWKS URL | (egress, not IAM) | JWKS validation cache fetches keys at startup and on `kid` miss. If running in a private subnet, ensure NAT or a VPC endpoint reaches the issuer. |

### Required if using OTLP export

| Action | Resource | Why |
|---|---|---|
| Network egress to `OTEL_EXPORTER_OTLP_ENDPOINT` | (egress, not IAM) | gRPC trace export. |

### Required if using MCP servers

Depends on which servers you enable. The default `filesystem` and
`fetch` servers in `mcp.config.json` need:

| Action | Resource | Why |
|---|---|---|
| Filesystem read on whatever path the MCP `filesystem` server is configured to expose | n/a (POSIX) | The subprocess inherits the worker's identity. |
| Network egress for MCP `fetch` | (egress) | If enabled. |

---

## 3. Infrastructure-as-code variables (Roadmap Critical #1 — `infra/` not yet implemented)

When `infra/tofu/` lands, these are the Terraform input variables an
operator will need to provide per environment. Listed here so the
deployment story is documented even before the IaC exists.

### Account / region

| Var | Type | Notes |
|---|---|---|
| `aws_account_id` | str | The 12-digit ID of the account this env deploys to. |
| `aws_region` | str | Must match what `BEDROCK_MODEL_ID`'s cross-region profile expects (e.g. `us-east-1`). |
| `env` | enum | `dev` / `staging` / `prod`. Used in tags + resource names. |

### Networking

| Var | Type | Notes |
|---|---|---|
| `vpc_id` | str | Existing VPC, or set `create_vpc=true` and let the module make one. |
| `private_subnet_ids` | list[str] | Where Fargate tasks run. Need NAT or VPC endpoints for outbound. |
| `public_subnet_ids` | list[str] | Where the ALB lives. |
| `allowed_cidr_blocks` | list[str] | Source CIDRs for the ALB security group. Set to `["0.0.0.0/0"]` if internet-facing; set to the upstream caller's CIDR if internal. |

### TLS / DNS

| Var | Type | Notes |
|---|---|---|
| `acm_cert_arn` | str | ACM certificate for the ALB HTTPS listener. Must be in the same region. |
| `route53_zone_id` | str | Optional. If set, the IaC creates an A-record alias for the ALB. |
| `domain_name` | str | Optional. e.g. `extract.<env>.example.com`. |

### Compute (ECS Fargate; replace with AgentCore-Runtime variables when ADR-0003 lands)

| Var | Type | Notes |
|---|---|---|
| `image_tag` | str | The GHCR image tag to deploy. e.g. `v0.3.0`. |
| `task_cpu` | int | vCPU units. `1024` = 1 vCPU. |
| `task_memory` | int | MiB. `2048` = 2 GB. |
| `desired_count` | int | Initial task count. Autoscaling adjusts from there. |
| `min_capacity` | int | Autoscaling floor. |
| `max_capacity` | int | Autoscaling ceiling. |

### Bedrock identity

| Var | Type | Notes |
|---|---|---|
| `bedrock_model_arns` | list[str] | The model ARNs the task role gets `bedrock:InvokeModel` on. Include the cross-region inference profiles. |

### Secrets / encryption

| Var | Type | Notes |
|---|---|---|
| `secrets_kms_key_arn` | str | Existing CMK, or let the module create one. |
| `api_keys_secret_arn` | str | If `AUTH_MODE` includes `apikey`. The Secrets Manager secret holds the CSV. |

### Observability

| Var | Type | Notes |
|---|---|---|
| `log_retention_days` | int | CloudWatch Logs retention. Recommended: `30` dev / `90` staging / `365` prod. |
| `otlp_endpoint` | str | Optional. If using a third-party trace backend (Datadog, Honeycomb, Grafana Cloud). Empty falls back to CloudWatch + X-Ray only. |
| `xray_sampling_rate` | float | `0.0` to `1.0`. Recommend `1.0` in dev, `0.1` in prod (Roadmap gap #12). |

### Alarms / paging — **TODO (deferred)**

When PagerDuty / Opsgenie integration lands (Roadmap Critical #3),
these variables will be required:

| Var | Type | Notes |
|---|---|---|
| `pagerduty_integration_key` | str (sensitive) | TODO. From the PagerDuty service's events-API integration. |
| `sns_alarm_topic_arn` | str | TODO. The SNS topic the burn-rate alarms publish to. PagerDuty subscribes to this topic. |
| `escalation_email_list` | list[str] | TODO. Fallback notification on SNS for shifts where no on-call is assigned. |

The CloudWatch burn-rate alarms themselves (fast 14.4× / 1h, slow 6× /
6h) can be defined in IaC today; they just need an alarm-action that
goes nowhere until the SNS topic is wired. Land the alarms first if
you want them visible; wire the paging when the on-call rotation is
ready.

---

## Bootstrapping a new environment — the short version

1. Provision an AWS account (or sub-account) for the env. Get the
   `aws_account_id` and decide the `aws_region`.
2. Decide auth model. `none` if behind a trusted gateway; otherwise
   create the API-key secret in Secrets Manager **or** the Cognito
   user pool + app client and capture issuer/audience/JWKS URL.
3. Create the GHCR image (or pin to an existing tag): the
   `release.yml` workflow handles tag-driven publishing.
4. (Once `infra/` lands) Run `tofu apply` in
   `infra/tofu/envs/<env>` with the variables above.
5. Smoke-test: `curl https://<gateway>/health` should return 200 with
   the right `service` and `version`. Then run the k6 smoke from
   `tests/load/` against the staging URL.
6. Wire **PagerDuty later** when the on-call rotation is ready.

---

## Related docs

- `docs/cost-breakdown.md` — per-request and monthly cost estimates.
- `docs/adr/0003-agentcore-full-platform-deployment.md` — the proposed
  AgentCore deployment shape.
- `docs/adr/0011-prompt-injection-threat-model.md` — what the auth /
  rate-limit settings defend against (and what they don't).
- `docs/runbooks/oncall.md` — on-call runbook (referenced by the
  PagerDuty TODO above).
- `.env.example` — the canonical, gitignored-in-spirit list of app env
  vars with the defaults this doc tabulates.
