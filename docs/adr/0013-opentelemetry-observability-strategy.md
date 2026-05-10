# ADR-0013: OpenTelemetry-first observability with metadata-only span attributes

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The service needs operator-visible signal for SRE triage (latency,
error rate, retry count) and developer-visible signal for behaviour
debugging (which schema, which attempt failed, which fields tripped
validators). It runs on AWS but customers may ship traces to vendor
backends (Datadog, Honeycomb, Grafana Cloud). The threat model
documented in [ADR-0011](0011-prompt-injection-threat-model.md)
treats document content as untrusted; observability must not become a
PII-leakage channel.

Three real choices on the table:

1. Vendor-specific SDK (`datadog`, `honeycomb-python`) — locks the
   service to one backend.
2. AWS X-Ray SDK — integrates with CloudWatch ServiceLens but has
   weaker non-AWS interoperability.
3. **OpenTelemetry** — vendor-neutral spans + metrics + logs, OTLP
   wire format, exporter pluggable per environment.

## Decision

OpenTelemetry across the board, configured in
`src/bedrock_strands_agent/telemetry.py`:

- **Tracer provider** with three exporter modes selected by env var:
  - `OTEL_EXPORTER_OTLP_ENDPOINT=…` → `OTLPSpanExporter` over gRPC
    (production / shared-collector path).
  - `STRANDS_OTEL_ENABLE_CONSOLE_EXPORT=true` → `ConsoleSpanExporter`
    (local dev only — must stay false in prod; the
    `security-reviewer` subagent enforces this).
  - Neither set → `TracerProvider` with no exporter (no-op tracer,
    zero overhead).
- **Auto-instrumentations**: `FastAPIInstrumentor` for HTTP server
  spans, `BotocoreInstrumentor` for Bedrock client spans. The
  Strands Agent layer is intentionally NOT auto-instrumented — its
  span output is too verbose at production traffic volume; we emit
  our own application-level spans instead.
- **Application spans** with stable names:
  - `extraction.run` — outer span on `/extract`, `/extract/document`.
  - `extraction.attempt` — child span per retry-loop attempt.
  - `extraction.stream` — outer span on `/extract/stream` (no
    `extraction.attempt` children by design — the streaming path
    bypasses the retry loop, see [ADR-0014](0014-streaming-retry-bypass.md)).
  - `extraction.vision_grounding` — child span on the second-pass
    grounding verifier (see [ADR-0016](0016-vision-grounding-second-pass.md)).
- **Sampling** uses the default `ParentBased(TraceIdRatioBased)`. In
  dev: `1.0` (capture everything). In prod: `0.1` is the recommended
  setting per [`cost-breakdown.md`](../cost-breakdown.md) — X-Ray
  costs scale linearly with retained trace count. An explicit
  per-deployment sampling-rate setting is roadmap-deferred (gap #12).
- **Span-attribute allowlist**: only `extraction.schema`,
  `extraction.schema_version`, `bedrock.model_id`,
  `extraction.mode`, `extraction.document_id` (caller-supplied
  correlation; not PII), `extraction.correlation_id`, latency / count
  metadata, and `extraction.image_count` for vision. **Never**
  `document_text`, `ExtractedField.value`, `source_excerpt`, or
  prompt content. Enforced via review by the
  `security-reviewer` subagent on every PR; covered by the static
  `no-print-of-document-text` semgrep rule for the logging surface.
- **Logs** flow through Python's stdlib logging with
  `pythonjsonlogger.JsonFormatter` and a `_ContextFilter` injecting
  `correlation_id` (a `ContextVar`) into every record. A
  `_RedactionFilter` masks SSN/EIN/email/phone/CC/AWS-key tokens on
  the formatted message and on string-valued `extra` attributes —
  defence-in-depth against accidental PII leakage even when log level
  is escalated.
- **Metrics** are exposed at `GET /metrics` via
  `prometheus-fastapi-instrumentator`. Out-of-the-box: HTTP request
  count + duration histogram tagged by status/method/path. Custom
  domain metrics (validator pass rate, citation verification rate,
  retry counts) are roadmap-deferred (gap #6).

## Consequences

**Positive**

- One API surface for traces, metrics, and logs that works across
  every backend: CloudWatch + X-Ray (default for AWS deploys), or any
  OTLP-compatible vendor.
- The metadata-only attribute discipline keeps PII out of the spans
  by construction. Operators can ship traces to a third-party
  collector without auditing every attribute against the customer
  contract.
- Span hierarchy reflects the retry-loop shape (`run → attempt`),
  making it cheap to query "show me all attempts that failed
  validation" without joining across runs.
- Auto-instrumentation handles the boring parts (HTTP timing, boto3
  call boundaries) while application spans capture the
  domain-specific events (which schema, which attempt, how many
  validator failures).

**Negative**

- Default sampling is `ParentBased` with no explicit ratio control.
  Operators who want production-rate sampling have to compose their
  own provider today; the per-deployment setting is deferred.
- No custom domain metrics — the SLOs in
  [`slos.md`](../slos.md) reference counters
  (`extraction_citation_verified_total`, etc.) that don't yet exist.
  This is a real gap to close in the eval-harness phase.
- Strands Agent internals are deliberately un-instrumented; if a
  prompt-engineering bug shows up in tool dispatch we have to add
  spans manually rather than reading them from auto-instrumentation.
  Acceptable for the current single-agent shape; would need
  revisiting if a swarm/graph topology is introduced.

## References

- [ADR-0001](0001-strands-sdk-over-langchain.md) — Strands SDK choice.
- [ADR-0011](0011-prompt-injection-threat-model.md) — the threat model
  whose Layer 4 (observability) this ADR implements.
- [ADR-0014](0014-streaming-retry-bypass.md) — explains why
  `extraction.stream` does not have `extraction.attempt` children.
- [ADR-0016](0016-vision-grounding-second-pass.md) — explains the
  `extraction.vision_grounding` sub-span.
- `src/bedrock_strands_agent/telemetry.py` — provider construction +
  instrumentation wiring.
- `src/bedrock_strands_agent/logging.py` — `_ContextFilter` and
  `_RedactionFilter`.
- `docs/cost-breakdown.md` — sampling-rate impact on X-Ray cost.
