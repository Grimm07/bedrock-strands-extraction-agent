# Roadmap

This file tracks intentional gaps. Each item is something that would
meaningfully harden or extend the service for a real production deploy but
was scoped out of an earlier release to keep that release reviewable.

## Released since 0.2.0

- **Coverage gate raised** from 80% to 85% (`pyproject.toml`); actual is
  ~93%.
- **OpenAPI typed-error coverage**: `/extract` now documents 404 + 502 with
  `ErrorResponse`; `/extract/document` documents 404 + 422 + 502 with
  `ErrorResponse`. Validation 422s on `/extract` continue to use FastAPI's
  default `HTTPValidationError` shape.
- **Boto3-layer e2e test** (`tests/test_bedrock_boto3_e2e.py`): patches
  `BaseClient._make_api_call` to drive a real `BedrockModel` end-to-end
  offline, asserting `modelId` and request shape on the
  `bedrock-runtime` `ConverseStream` call. Catches BedrockModel-wiring
  regressions the higher-layer Agent mocks cannot see.

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

## Not yet shipped — UX / DX

### Streaming endpoint

Long extractions return only after the model completes. Add a
`/extract/stream` SSE endpoint that streams partial fields as the model
emits them. Strands supports streaming via `Agent.stream_async`.

### Async agent invocation

`Agent.__call__` is sync; the FastAPI route blocks an event-loop slot per
request. Switch to `Agent.acall` (or run the sync call in a thread pool)
once the SDK API stabilises.

### Request-side schema versioning

`SchemaDefinition` already carries `version` and the registry exposes it on
`/schemas`, but `ExtractRequestBody.schema_name` is still a bare name. Add
an optional `schema_version` to the request body so callers pin to e.g.
`invoice@1.0.0` rather than tracking HEAD of the registry.

## Not yet shipped — observability / quality

### ADRs still to record

The following decisions are worth ADRs, in addition to the five already
filed (0001–0004, 0008): Bedrock as default provider, JSON-only response
contract, MCP-as-tools, retry-once-then-fail, ≥85% coverage gate.
