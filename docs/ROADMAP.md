# Roadmap

This file tracks intentional gaps. Each item is something that would
meaningfully harden or extend the service for a real production deploy but
was scoped out of an earlier release to keep that release reviewable.

## Released in 0.2.0 (Phase A + Phase C partial)

- Pre-commit, gitleaks, semgrep custom rules, Renovate (replaces Dependabot
  for pip+docker), `.devcontainer`, k6 load test, mkdocs site, ADRs 0001–0004
  + 0008, runbooks, SLO doc.
- Service hardening: FastAPI auth middleware (apikey / Cognito JWT / both),
  slowapi rate-limiting, tenacity-backed Bedrock retry. v0.2 ROADMAP items
  pulled forward.
- Accuracy: PII redaction module, prompt template versioning, validators
  refactored into `extraction.validators`, citation verification via
  `extraction.citations`, weighted overall-confidence calibration
  ([ADR-0004](adr/0004-confidence-calibration.md)), multi-shot self-
  correcting retry that surfaces validator + citation failures back to the
  model.
- **Document upload + multimodal vision path**
  ([ADR-0008](adr/0008-extraction-modes-text-vs-vision.md)):
  `POST /extract/document` accepts PDF (text via pypdf) or image
  (PNG/JPEG/WebP/GIF → Bedrock Converse multimodal). No separate OCR
  engine. Pulled forward from the v0.3 deferral.

## Shipped in v0.1

- **Self-correcting retry loop.** On schema-validation failure, the service
  re-prompts with `retry.j2` listing the specific errors. Capped at one retry
  (`ExtractionService(..., max_retries=1)`).
- **Live-Bedrock integration test.** Marked `integration`, skipped by default
  unless `RUN_INTEGRATION_TESTS=1`.
- **Structured error body for 500s.** The global exception handler returns
  `ErrorResponse{detail, correlation_id}`.

## Not yet shipped — production blockers

These should be in place before this service handles real customer documents.

### Auth

The service has no built-in authentication. In v0.1 it assumes deployment
behind a gateway / sidecar / mesh that handles auth. Direct exposure to the
public internet is not safe.

**Options for v0.2**:
- Static `X-API-Key` header check (simple, fast).
- JWT validation (issuer + audience + scopes).
- mTLS (delegated to a sidecar like Envoy).

### Rate limiting

Nothing throttles `/extract`. A misbehaving caller can fan out into Bedrock
and bury the service in cost or hit Bedrock RPS limits. Use a sidecar or
add `slowapi` per-IP and per-API-key buckets.

### Bedrock retries / fallbacks

`BedrockModel` throws on transient throttling. Wire a tenacity-based retry
around `Agent.__call__` with exponential backoff for `ThrottlingException`
and `ModelStreamErrorException`.

## Not yet shipped — UX / DX

### Streaming endpoint

Long extractions return only after the model completes. Add a
`/extract/stream` SSE endpoint that streams partial fields as the model
emits them. Strands supports streaming via `Agent.stream_async`.

### Async agent invocation

`Agent.__call__` is sync; the FastAPI route blocks an event-loop slot per
request. Switch to `Agent.acall` (or run the sync call in a thread pool)
once the SDK API stabilises.

### Request schema versioning

`/extract` takes a free-form `schema_name`. Add an optional `schema_version`
so callers pin to a tested version of `invoice@1.0.0` rather than tracking
HEAD.

## Not yet shipped — observability / quality

### Coverage gate

Currently 80%; actual is ~89%. Worth tightening to 85% to catch coverage
regressions earlier. Gaps are concentrated in `agent/builder.py` (the
real-Bedrock path) and `agent/mcp.py` (the real-stdio-server path).

### Boto3-layer e2e test

The integration test mocks at the Strands `Agent` level. A complementary
test that mocks at the `boto3` Bedrock client would catch regressions in
the BedrockModel wiring itself.

### Error response wiring

`ErrorResponse` is referenced by the OpenAPI schema for 500 only. Add it as
the `responses=` argument on `/extract` so 404 and 502 also document the
typed body.

### Confidence calibration

`overall_confidence` is a flat average of per-field confidences. A weighted
average (required > optional) would be more useful for SLAs and downstream
gating.

### ADRs

The decisions worth recording: Bedrock as default provider, JSON-only
response contract, MCP-as-tools, retry-once-then-fail, ≥80% coverage gate.

## Not yet shipped — repo hygiene

- **`lefthook` / `pre-commit`** — wire up `make check` on commit.
- **`.devcontainer`** — one-click VS Code setup.
- **Load test** — `k6` or `locust` script with a stub backend.
- **Renovate** in addition to Dependabot for tighter version pinning policy.
