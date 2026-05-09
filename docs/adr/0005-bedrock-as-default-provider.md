# ADR-0005: Amazon Bedrock as the default model provider

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

ADR-0001 settled the agent framework question (Strands SDK over LangChain) but
left the model-provider question open. Strands is provider-agnostic — it ships
adapters for Bedrock, the Anthropic API direct, OpenAI, and several others —
so a separate decision was required for which backend the service binds to by
default.

The forces at play:

- **Deployment target.** This service is built for AWS-resident enterprises.
  Every existing and prospective consumer already runs in an AWS account,
  with IAM, KMS, VPC egress controls, CloudTrail, and centralised billing
  already wired up.
- **Auth model.** Anything the runtime calls must fit the standard boto3
  credential chain (instance role on AgentCore / ECS, OIDC on CI, SSO
  locally). A second long-lived API key would be a new secret to rotate and
  a new break-glass story to maintain.
- **Egress and data residency.** Customer documents passed to the model must
  not leave the AWS region the workload runs in. This is a contractual
  constraint for the form-extraction use case, not a preference.
- **Schema-first contract.** ADR-0002 commits us to Pydantic-validated
  structured output. Whichever backend we pick has to expose a tool-use /
  structured-output surface that survives our retry loop without bespoke
  per-provider parsing.
- **Model availability.** Claude is the target family (ADR-0001 context). We
  need a path to the current Sonnet generation in `us-east-1` and
  `us-west-2`, including the cross-region inference profiles.

## Decision

The default model provider is **Amazon Bedrock**, accessed through
`strands.models.bedrock.BedrockModel`. The default model is **Claude Sonnet
4.6** (`us.anthropic.claude-sonnet-4-6-20250514-v1:0`, the cross-region
inference profile). Region is read from the standard `AWS_REGION` env var
via the boto3 credential chain (see `aws_region` in `config.py`); no
service-specific region override exists today. The model id is
overridable per-deployment via `BEDROCK_MODEL_ID` so customers on a different
allow-list (e.g. Haiku for cost, an older Sonnet for stability) do not need
a fork.

Construction lives in one place — `agent/builder.py` — so a future provider
swap is a localised change rather than a cross-cutting refactor.

**Alternatives considered**

- **Anthropic API direct.** Rejected. It introduces a second vendor
  relationship, a second billing line, a second IAM-equivalent (API key
  rotation), and no native control over which region the request egresses
  to. The latency story is fine but the governance story is worse for our
  customer profile.
- **OpenAI API.** Rejected. Same multi-vendor cost as above, and at the time
  of writing its structured-output behaviour does not match Claude's
  tool-use semantics closely enough to share a single retry path with the
  Pydantic contract; we would end up maintaining two parsers.
- **Multi-provider abstraction from day one.** Rejected as premature. The
  cost of a provider-neutral seam is real (lowest-common-denominator
  feature surface, doubled test matrix, harder error attribution) and we
  have one customer profile today. We prefer to pay that cost when we have
  a second concrete consumer, not speculatively.

## Consequences

**Positive**

- **One IAM scope.** The runtime needs `bedrock:InvokeModel` (and the
  cross-region inference variants) on the model ARNs it actually uses.
  Credentials flow through the standard boto3 chain — no new secret
  material, no new rotation runbook.
- **Telemetry is free.** Request-level audit lands in CloudTrail and metrics
  in CloudWatch with no extra wiring; Phase D telemetry only has to layer
  application-level spans on top.
- **Data residency holds.** Bedrock invocations stay within the configured
  region (modulo the explicit cross-region inference profile, which is
  itself US-only and documented).
- **Single seam to swap.** Provider construction is centralised in
  `src/bedrock_strands_agent/agent/builder.py:46`. Replacing the backend is
  a one-file change plus its tests, not a sweep.

**Negative**

- **Regional model availability is now a deployment concern.** Claude 4.x is
  available in `us-east-1` and `us-west-2`; reaching it from other regions
  requires the `us.` cross-region inference profile, which has to be
  explicitly enabled in the account. Customers in `eu-*` or `ap-*` regions
  must either accept the cross-region hop or pin to a model that is
  resident in their region.
- **Off-AWS customers are not served.** Anyone who must stay off AWS
  entirely cannot use this service as-is; they would need to self-host or
  put a Bedrock-compatible proxy in front of it. We accept this trade-off
  because it is a non-goal today.
- **Provider switch is non-trivial in practice.** Even though the code seam
  is small, swapping providers means re-validating retry semantics,
  structured-output behaviour, throttling characteristics, and the
  telemetry mapping. The "one-line edit" is the cheap part of the change.

## References

- ADR-0001 — Strands SDK over LangChain (framework choice that this ADR
  follows from).
- ADR-0002 — schema-first extraction with self-correcting retry (sets the
  structured-output requirement that constrained provider choice).
- `src/bedrock_strands_agent/agent/builder.py:36-72` — `BedrockModel`
  construction, region resolution, and model-id override.
- `src/bedrock_strands_agent/config.py` — Bedrock-related settings
  (`aws_region` reads `AWS_REGION`; `BEDROCK_MODEL_ID` overrides the
  default model id).
