# ADR-0003: AgentCore full-platform deployment

- **Status:** Proposed
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

> This ADR captures the deployment direction set in the production-readiness
> plan. It is **Proposed** until Phase B lands the IaC and the first dev-cloud
> smoke test passes; at that point a `## Status update` section moves it to
> **Accepted** with the actual resource ARNs and IAM trust details.

## Context

The service is a stateless FastAPI app today, runnable as a generic container.
For production we need:

- A managed agent runtime that does not require us to operate ECS/EKS task
  definitions, ALB listeners, or a service mesh.
- An identity layer that mints per-tenant tokens we can scope and audit.
- An audit trail of extraction events that compliance can query without us
  building a separate database.
- An external entrypoint with first-class authentication and rate-limit.
- AWS-native observability (CloudWatch + X-Ray) so on-call uses the same
  console as the rest of the org's services.

AWS Bedrock AgentCore — Runtime + Identity + Memory + Gateway — covers all
four pillars. The Terraform AWS provider v6.33.0 has GA `aws_bedrockagentcore_*`
resources, removing the need for the `awscc` bridge.

## Decision

Deploy to **AgentCore full platform**:

| Pillar       | Service                                            | Use here                                                                  |
| ------------ | -------------------------------------------------- | ------------------------------------------------------------------------- |
| **Runtime**  | `aws_bedrockagentcore_agent_runtime`               | Hosts the container (ECR image), `custom_jwt_authorizer` validates tokens |
| **Identity** | Cognito user pool + `aws_bedrockagentcore_workload_identity` | OAuth2 client_credentials per tenant; workload identity is a ready-slot for outbound MCP OAuth |
| **Memory**   | `aws_bedrockagentcore_memory` (audit-only)         | Append-only event log keyed by `tenant_id` + `correlation_id`             |
| **Gateway**  | `aws_bedrockagentcore_gateway` (`MCP` protocol)    | External entrypoint, JWT auth, WAFv2 rate-based rule for per-IP throttle  |

**Code-level changes** (Phase B§B4): a new `bedrock_strands_agent.agentcore`
package with `entrypoint.py` (using `BedrockAgentCoreApp` + `@app.entrypoint`
to delegate to `ExtractionService`), `memory.py` (write-only audit), and
`identity.py` (extract `custom:tenant_id` from authorizer claims). A
`docker/entrypoint.sh` script switches between FastAPI (`RUNTIME_MODE=fastapi`)
and AgentCore (`RUNTIME_MODE=agentcore`) so local dev still uses uvicorn.

**Out of scope (deferred):** AgentCore Code Interpreter and Browser Tool —
stateless form extraction has no current need; we provision the four pillars
only and add advanced services via the same module pattern when justified.

## Consequences

**Positive**

- One pane of glass for ops: CloudWatch dashboards, X-Ray service map,
  AgentCore Memory audit, Gateway 4xx/5xx counters all in the AWS console.
- Per-tenant isolation is a Cognito-app-client + Memory-`actor_id` pair, not
  a custom database schema.
- Provider-managed runtime means we do not own EKS/ECS upgrades, AMI patching,
  or ALB cost.
- Workload identity is provisioned as a future hook for MCP servers requiring
  OAuth (e.g. Slack tools); no consumer today, but the slot is ready.

**Negative**

- AgentCore is a young service. Provider features (rate-limiting on Gateway,
  weighted/canary traffic shifting) lag behind ECS-Fargate parity; we will
  re-evaluate canary support in 6.34+. Today: blue-green via image tag swap,
  WAFv2 for rate-based throttle.
- Cold-start latency on Runtime is not user-controllable. Phase E2 adds a
  pre-warmer (CloudWatch Events scheduled `extract` ping) if measured p95
  exceeds budget.
- The defense-in-depth FastAPI auth middleware (Phase A9) is still required —
  in `RUNTIME_MODE=agentcore` it is configured to `auth_mode=apikey` (Runtime's
  `custom_jwt_authorizer` validates JWT first). The toggle is documented in
  `docs/runbooks/auth-misconfig.md`.

## References

- `infra/tofu/modules/agentcore-runtime/`, `agentcore-memory/`, `agentcore-gateway/`,
  `agentcore-identity/` — to be added in Phase B.
- `src/bedrock_strands_agent/agentcore/` — to be added in Phase B§B4.
- ADR-0001 — Strands SDK choice (works with AgentCore via `BedrockAgentCoreApp`).
- ADR-0002 — schema-first extraction (unchanged on AgentCore).
