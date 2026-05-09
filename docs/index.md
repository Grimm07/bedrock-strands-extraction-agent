# bedrock-strands-agent

A schema-driven form-extraction agent built on the
[Strands Agents SDK](https://github.com/strands-agents/sdk-python) with
Amazon Bedrock as the model provider and FastAPI as the HTTP front door.

## What it does

Given a registered `FormSchema` (e.g. IRS W-9, invoice, IRS 1099-NEC) and a
document, the service returns structured JSON with field values, model
self-assessed confidence, and verbatim source excerpts. Validation failures
trigger one self-correcting retry against the same model with the precise
errors threaded back into the prompt.

## Where to start

| You want to…                                    | Read                                                                                  |
| ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| Run it locally                                  | [`README.md`](https://github.com/your-org/bedrock-strands-agent#quick-start)          |
| Add a new form schema                           | The `add-form-schema` skill in `.claude/skills/`                                      |
| Understand why we picked Strands                | [ADR-0001](adr/0001-strands-sdk-over-langchain.md)                                    |
| Understand the extraction contract              | [ADR-0002](adr/0002-schema-first-extraction-with-self-correcting-retry.md)            |
| Page-out support                                | [Runbooks index](runbooks/oncall.md)                                                  |
| Operate it (SLOs)                               | [Service Level Objectives](slos.md)                                                   |
| Understand the production deployment direction  | [ADR-0003](adr/0003-agentcore-full-platform-deployment.md)                            |

## Architecture at a glance

```
caller ──▶ Gateway (Phase B) ──▶ AgentCore Runtime ──▶ FastAPI app
                                       │
                                       ├── AuthMiddleware (apikey | JWT)
                                       ├── slowapi rate-limit
                                       ├── ExtractionService
                                       │     ├── Jinja2 prompts (StrictUndefined)
                                       │     ├── Strands Agent (BedrockModel + tools)
                                       │     └── self-correcting retry
                                       ├── OpenTelemetry → CloudWatch + X-Ray
                                       └── AgentCore Memory (audit only)
```

## Status

The repo is at v0.2.0-pre, mid-rollout of the production-readiness plan
([`/.claude/plans/review-the-current-code-smooth-anchor.md`](https://github.com/your-org/bedrock-strands-agent/blob/main/.claude/plans/review-the-current-code-smooth-anchor.md)).
Phase A (SDLC + service hardening) is the current commit set; subsequent phases
add OpenTofu IaC for AgentCore (Phase B), accuracy levers (Phase C),
observability + layered eval (Phase D), and gap synthesis (Phase E).
