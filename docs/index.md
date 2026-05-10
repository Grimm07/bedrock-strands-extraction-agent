# bedrock-strands-agent

A schema-driven form-extraction agent built on the
[Strands Agents SDK](https://github.com/strands-agents/sdk-python) with
Amazon Bedrock as the model provider and FastAPI as the HTTP front door.

## What it does

Given a registered `FormSchema` (e.g. IRS W-9, invoice, IRS 1099-NEC) and a
document, the service returns structured JSON with field values, model
self-assessed confidence, and verbatim source excerpts. Validation failures
trigger up to two self-correcting retries against the same model with the
precise errors threaded back into the prompt (capped per ADR-0009).

Three input paths — pre-extracted text, multipart upload (PDF or image
auto-routed to text or vision; scanned PDFs rasterise server-side via
`pypdfium2`), and Server-Sent Events streaming — share one schema-driven
contract. Schemas can be pinned per-request via `schema_version`.

## Where to start

| You want to…                                    | Read                                                                                  |
| ----------------------------------------------- | ------------------------------------------------------------------------------------- |
| Run it locally                                  | [`README.md`](https://github.com/Grimm07/bedrock-strands-extraction-agent#quick-start)          |
| See every library and why it is in the tree     | [Technology stack](tech-stack.md)                                                     |
| Add a new form schema                           | The `add-form-schema` skill in `.claude/skills/`                                      |
| Pick the right endpoint (text / vision / stream) | [Extraction modes](extraction-modes.md)                                              |
| Understand why we picked Strands                | [ADR-0001](adr/0001-strands-sdk-over-langchain.md)                                    |
| Understand the extraction contract              | [ADR-0002](adr/0002-schema-first-extraction-with-self-correcting-retry.md)            |
| Page-out support                                | [Runbooks index](runbooks/oncall.md)                                                  |
| Operate it (SLOs)                               | [Service Level Objectives](slos.md)                                                   |
| Understand the production deployment direction  | [ADR-0003](adr/0003-agentcore-full-platform-deployment.md)                            |
| See what shipped vs. what's still deferred      | [Roadmap](ROADMAP.md)                                                                 |

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

The repo is at **v0.3.0** — Phase A (SDLC + service hardening), Phase C
partial (accuracy levers, weighted confidence calibration, citation
verification), and the Phase D extraction-surface follow-on (multimodal
vision, scanned-PDF rasterisation, streaming SSE, request-side schema
versioning, non-blocking route handlers) have all shipped. See the
[`Roadmap`](ROADMAP.md) for what's still deferred — primarily the
`infra/` IaC for AgentCore (Phase B) and the Phase D5 evaluation
harness. The [`CHANGELOG`](https://github.com/Grimm07/bedrock-strands-extraction-agent/blob/main/CHANGELOG.md)
has the full per-version breakdown.
