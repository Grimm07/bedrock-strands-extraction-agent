# ADR-0012: Expose the extraction service over A2A (agent-to-agent protocol)

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

Other agents in the broader system increasingly need to call this
extraction service as a capability — most clearly, an orchestrator
agent that decides "this incoming form is an invoice; route it to the
extractor". The HTTP `/extract` and `/extract/document` endpoints are
fine for human-built clients, but the emerging open standard for
agent-to-agent communication is **A2A** (`a2aproject.org`,
`a2a-sdk` Python package), a JSON-RPC 2.0 protocol with a discovery
shape (the AgentCard at `/.well-known/agent-card.json`) and a small
core verb set (`message/send`, `tasks/get`, `tasks/cancel`,
`tasks/resubscribe`).

Strands ships a built-in A2A integration
(`strands.multiagent.a2a.A2AServer`, `StrandsA2AExecutor`) that wraps
a Strands `Agent`. Using it directly would surface our underlying
agent's free-text Bedrock response as the A2A response, with two
problems:

1. **The schema-first contract is lost.** The whole point of this
   service is the schema-driven pipeline: `_parse_json` →
   `_coerce_fields` → `validate_value` → citation verification → retry
   → vision-mode grounding (ADR-0011). An A2A caller wrapping our
   agent would see the model's free text and have to re-run validation
   themselves — duplicating logic the service exists to encapsulate.
2. **Vision-mode grounding and retry are skipped.** Strands' executor
   calls `agent.stream_async`, which in our codebase does not go
   through `ExtractionService` and therefore does not run the
   vision-grounding second-pass call or the validator-error retry
   prompt.

A2A is otherwise a good fit: pluggable transport, well-defined
agent-card metadata, structured `DataPart` for typed payloads.

## Decision

Mount A2A endpoints **inside the existing FastAPI app** with a custom
executor that wraps `ExtractionService` directly:

- `GET /.well-known/agent-card.json` — canonical A2A discovery path,
  unauthenticated by design (the card is public metadata; the actual
  capability behind it is gated separately).
- `GET /a2a/.well-known/agent-card.json` — namespaced copy for clients
  that look there.
- `POST /a2a/jsonrpc` — the JSON-RPC 2.0 endpoint that handles
  `message/send`, `tasks/get`, etc. Goes through the same
  `AuthMiddleware`, slowapi rate-limiting, `CorrelationIdMiddleware`,
  and OTel instrumentation as `/extract` and `/extract/document`.

The executor (`bedrock_strands_agent.a2a.executor.ExtractionAgentExecutor`)
extends `a2a.server.agent_execution.AgentExecutor` and:

- **Requires a `DataPart` payload.** The A2A message must carry a
  DataPart with at least `schema_name` and `document_text`. Optional
  fields: `schema_version` (pin), `document_id` (correlation id).
  TextPart-only requests are rejected with a clear error pointing the
  caller to the DataPart shape.
- **Routes through `ExtractionService.extract`.** The full pipeline
  runs: schema resolution, prompt rendering, model invocation, JSON
  parsing, validators, citation verification, retry. Vision-mode
  grounding is automatic when `extract_document` is the entry point
  (not currently exposed via A2A; see Consequences).
- **Returns the `ExtractionResult` as a `DataPart` artifact.** The
  caller gets the same JSON shape as the HTTP `/extract` endpoint, so
  contract parity is preserved across transports.
- **Rejects cancellation.** Extractions are short-lived (single-digit
  seconds happy-path, tens of seconds with grounding + retries); the
  executor declines `tasks/cancel` with `UnsupportedOperationError`.

The integration is **opt-in** per environment. `A2A_ENABLED=false`
(default) leaves the routes unmounted; `A2A_ENABLED=true` mounts them.
`A2A_PUBLIC_URL` controls the URL advertised in the agent card; unset
falls back to a `http://{api_host}:{api_port}/a2a/jsonrpc`
construction suitable for local development.

**Alternatives considered:**

- **Use `strands.multiagent.a2a.A2AServer` directly** — rejected for
  the contract-loss reasons above. Could be revisited if a future
  workload truly wants the free-text agent surface (it doesn't today).
- **Mount A2A as a Starlette sub-app via `app.mount('/a2a', sub_app)`**
  — rejected. Sub-app mounts shadow the parent middleware stack, so
  the A2A endpoints would lose `AuthMiddleware`, slowapi, and
  `CorrelationIdMiddleware`. Splicing the routes into the existing app
  preserves the middleware stack.
- **A2A-only (drop the HTTP `/extract*` routes)** — rejected. HTTP is
  still the cheapest integration for human-built clients (curl, the
  load tests, simple scripts). A2A is additive, not a replacement.
- **Vision via A2A** — deferred. The current A2A surface only handles
  text-mode extractions. Adding image upload over A2A means handling
  `FilePart` (which carries `bytes` or `uri`), routing through
  `extract_document`, and re-thinking the trust-boundary story for
  image content received over JSON-RPC. Out of scope; tracked as a
  follow-on.

## Consequences

**Positive**

- A2A-compliant orchestrators (Strands, Google's reference clients,
  any agent that speaks the protocol) can call this service without
  custom HTTP integration.
- Contract parity: `/a2a/jsonrpc` and `/extract` produce the same
  `ExtractionResult` shape. A caller can switch transports without
  changing downstream parsing.
- Discovery is standards-compliant: `/.well-known/agent-card.json` is
  the canonical A2A path, so off-the-shelf clients find the capability.
- All the security work (input bounds, prompt-injection delimiter,
  trust-boundary system prompt, citation verification, value-anchor
  check, vision grounding, log redaction) flows through automatically
  because A2A messages route through `ExtractionService.extract`.

**Negative**

- The A2A surface is **text-mode only** today. `/extract/document` is
  not exposed; vision callers stay on HTTP. ADR-0008 + ADR-0011's
  vision pipeline would need an A2A `FilePart` adapter to surface; the
  trust-boundary story for FilePart-supplied images is not
  trivially the same as for caller-uploaded multipart files.
- The `InMemoryTaskStore` ships by default. Restarting the service
  loses any in-flight tasks. Acceptable for synchronous extractions
  (the task completes within a single request anyway), but a future
  resubscribe / push-notifications workflow would need a persistent
  store (DynamoDB, Postgres, etc.).
- Cancellation is not supported. Acceptable today — extractions are
  short — but a future streaming/long-running variant would need a
  cooperative-cancel handshake.
- The agent card is currently unsigned. A2A allows
  `AgentCardSignature`; we don't sign today. If/when a customer
  requires signed cards (cross-tenant trust), this is a bolt-on.
- A2A's auth-scheme negotiation (`securitySchemes` in the card) is
  not yet wired. The card simply advertises no security scheme; the
  JSON-RPC endpoint goes through whatever `AuthMiddleware` is
  configured (apikey / JWT / both). Operators must communicate the
  required auth scheme out-of-band. Wiring `securitySchemes` is a
  small follow-on.

## References

- ADR-0001 — Strands SDK choice (the framework whose A2A integration
  inspired this; we deliberately wrap our own pipeline instead).
- ADR-0007 — MCP-as-tools (the OTHER agent-extension protocol;
  complementary to A2A — MCP is "I expose tools to agents", A2A is
  "I expose myself as an agent").
- ADR-0008 — extraction modes (text vs vision; A2A surface today is
  text-only).
- ADR-0011 — prompt-injection threat model (A2A messages are subject
  to the same Layer-1 input bounds and Layer-2 prompt-template
  hygiene because they route through the same `ExtractionService`).
- `src/bedrock_strands_agent/a2a/` — the A2A module (executor,
  agent_card, routes).
- `tests/test_a2a.py` — the end-to-end test battery (15 tests).
- A2A protocol specification — `https://a2aproject.org/`,
  `a2a-sdk` PyPI package (`a2a-sdk>=0.3,<0.4`).
