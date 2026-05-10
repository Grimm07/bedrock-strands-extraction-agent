# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added (consumer-facing docs + ADR backfill)

- **`docs/consumer-guide.md`**: end-to-end walkthrough for engineers
  calling the service. Covers all four transports (HTTP JSON,
  multipart, SSE streaming, A2A JSON-RPC) with request/response
  shapes, schema discovery and registration, auth setup, error-mode
  triage, observability hooks, cost summary, and a production
  deployment checklist. Surfaced in the `mkdocs.yml` nav and the
  README.
- **5 backfilled ADRs (0013–0017)** capturing decisions visible in
  the code but not yet recorded:
  - 0013 OpenTelemetry-first observability with metadata-only span
    attributes (sampling, instrumentation choice, attribute allowlist).
  - 0014 `/extract/stream` bypasses the self-correcting retry loop
    (rationale + alternatives).
  - 0015 `asyncio.to_thread` offload at the route boundary instead
    of native `Agent.invoke_async` (with the explicit reasoning
    against the full async port).
  - 0016 Vision-mode grounding via a second model call — the
    automation, no-human-review framing for [ADR-0011](docs/adr/0011-prompt-injection-threat-model.md)'s
    last Critical follow-on.
  - 0017 Two-layer test mocking strategy (Strands Agent layer for
    behaviour, boto3 `BaseClient._make_api_call` for wiring
    regression).
- **Refreshed `docs/adr/README.md` index** to list all 17 ADRs (was
  stale, listed only 5).
- **README**: rewrote the lede to point at `docs/consumer-guide.md`
  and `docs/tech-stack.md` as the primary entry points; trimmed the
  config table to most-edited settings (full checklist remains in
  `docs/deployment-variables.md`).

### Changed (vision grounding contract tightening)

- **Vision-grounding verifier response is now Pydantic-validated.**
  The previous hand-rolled walker (`isinstance(entry, dict)` +
  `isinstance(name, str)` + `entry.get("present")`) is replaced by
  two private Pydantic models, both with `extra="forbid"` and
  `present: StrictBool`:
  - `_GroundingItem(name: str, present: StrictBool)`
  - `_GroundingResponse(groundings: list[_GroundingItem])`
  Validated through `_GroundingResponse.model_validate(...)` inside
  `_verify_vision_grounding`. **Behaviour change**: any deviation
  (missing key, wrong type, *extra* key, or a non-strict-bool like
  `"yes"` / `1`) fails the **whole** response and falls back to "no
  failures" (fail open) — previously the walker silently skipped
  malformed entries while keeping the well-formed ones. The strict
  + fail-open contract is safer at the threat-model boundary
  (the verifier sits between an attacker-controlled image and our
  trust decisions). New regression test:
  `tests/test_api.py::test_extract_document_grounding_partially_malformed_fails_open`.
  A future migration to `BedrockModel.structured_output` (which uses
  Bedrock tool-use to force the shape at the model layer) is
  deferred — it requires making `_verify_vision_grounding` async,
  threading an `AsyncGenerator` consumer through the sync extraction
  loop, and routing vision content through a `BedrockModel` instance
  instead of the bespoke `invoke_multimodal` Converse call.

### Added (A2A protocol)

- **A2A (agent-to-agent) protocol integration**
  ([ADR-0012](docs/adr/0012-a2a-protocol.md)): when `A2A_ENABLED=true`,
  the FastAPI app mounts `GET /.well-known/agent-card.json` (canonical
  A2A discovery), `GET /a2a/.well-known/agent-card.json` (namespaced
  copy), and `POST /a2a/jsonrpc` (JSON-RPC 2.0 endpoint). A custom
  `ExtractionAgentExecutor` wraps `ExtractionService.extract` directly
  rather than using `strands.multiagent.a2a.StrandsA2AExecutor`, so
  the schema-first pipeline (validators, citation verification, retry
  loop, prompt-injection defences) flows through unchanged.
- A2A messages must carry a `DataPart` payload with `schema_name` +
  `document_text` (optional `schema_version`, `document_id`).
  TextPart-only requests fail with a clear error directing the caller
  to the structured shape. The validated `ExtractionResult` is
  returned as a `DataPart` artifact named `extraction_result`.
- Discovery is unauthenticated by spec; the canonical and namespaced
  agent-card paths are added to `AUTH_EXCLUDED_PATHS`. The JSON-RPC
  endpoint goes through `AuthMiddleware` like every other API surface.
- New env vars: `A2A_ENABLED` (bool, default `false` — opt-in
  per-environment) and `A2A_PUBLIC_URL` (the URL advertised in the
  agent card; falls back to `http://{api_host}:{api_port}/a2a/jsonrpc`
  for local dev). Documented in `.env.example` and
  `docs/deployment-variables.md`.
- New tests in `tests/test_a2a.py` (15 cases): agent-card discovery
  (canonical + namespaced + URL fallback + explicit override), happy-
  path `message/send` returning the `ExtractionResult` artifact, error
  cases (TextPart-only, missing required field, unknown schema),
  defence-in-depth checks that A2A routes are absent when disabled and
  that the agent card bypasses auth while the JSON-RPC endpoint does
  not.
- Vision-mode A2A is deliberately deferred: the current surface is
  text-only. Vision over A2A would need a FilePart adapter and a fresh
  trust-boundary review for image content over JSON-RPC.

### Added (operational readiness)

- **k6 CI gate against the stub-mode app**: new
  `.github/workflows/load-test.yml` boots `scripts/_boot_with_stub.py`
  (no Bedrock calls), waits for `/health`, and runs a 30-second
  `ci_smoke` scenario with the production SLO thresholds (p95 < 3 s,
  error rate < 1%). Catches HTTP-routing, middleware, and event-loop
  regressions on every PR — for free, no Bedrock spend. The existing
  `smoke` (5 RPS / 2 min) and `soak` (50 RPS / 5 min) scenarios remain
  for manual / release-time runs against real Bedrock; pick the
  scenario set with `K6_PROFILE=ci|smoke|soak|full`.
- **`docs/cost-breakdown.md`**: per-request Bedrock token math, monthly
  cost bands at three traffic profiles, compute / observability /
  networking line items, and an ordered list of cost-reduction levers.
  Bedrock dominates by ~100× over infra; the doc names model selection,
  conditional grounding, retry-budget tightening, and document-hash
  caching as the highest-leverage knobs.
- **`docs/deployment-variables.md`**: full operator checklist for
  standing up a new env — application env vars (with REQUIRED markers),
  AWS-side IAM scopes, and the Terraform input variables the deferred
  `infra/` module will need. Calls out the PagerDuty / SNS-topic
  integration as a deferred TODO.

### Added (Phase D — Vision-mode grounding)

- **Vision-mode second-pass grounding** ([ADR-0011](docs/adr/0011-prompt-injection-threat-model.md)
  Critical follow-on closed): every `/extract/document` image extraction
  now runs a second `invoke_multimodal` call with a verification prompt
  (`templates/verify_grounding.j2`) asking the model to confirm each
  candidate value is genuinely present in the attached image. Ungrounded
  fields surface as `vision_grounding_failures` and trigger the existing
  retry loop with vision-specific instructions in `templates/retry.j2`
  (v3.2.0). Failure to parse the verifier's response fails open — the
  first-pass schema validation still constrains the result. Doubles
  per-vision-request latency / cost; accepted given the automation goal
  named in the threat-model discussion.
- New `PromptRenderer.verify_grounding(schema, candidates)` and
  `ExtractionService._verify_vision_grounding`. The retry loop's
  `_run_extraction_loop` gains an optional `post_coerce_hook` so vision
  mode can plug grounding in without coupling the text-mode path to it.
- `extraction.attempt` and `extraction.vision_grounding` spans expose
  per-attempt grounding-failure counts.

### Added (Phase D — LLM-security defence-in-depth)

- **Logging-side PII redaction filter** (`bedrock_strands_agent.logging._RedactionFilter`):
  attaches to the root logger alongside the correlation-id filter, masks
  US PII tokens (SSN, EIN, email, US phone, credit card, AWS / sk_ / pk_
  keys) on both the formatted message AND any `extra={...}` attribute that
  semgrep cannot statically see. Closes the HIGH appsec gap from ADR-0011
  (Phase D4 was deferred from v0.1; now wired).
- **MCP response sanitiser** (`agent.mcp._SanitisingMCPTool`,
  `_wrap_tools_with_sanitiser`): every MCP tool's responses pass through a
  `MCP_RESPONSE_TEXT_CAP=8 KiB` truncation + the same redaction patterns
  before the model sees them. Defence-in-depth against indirect prompt
  injection through compromised or attacker-controlled MCP servers.
  Non-text content (image/document/json) passes through untouched.
- **Value-anchored citation verification**
  (`extraction.citations.value_anchored_in_excerpt`): closes the
  schema-confusion gap from ADR-0011 where the model could emit a
  verbatim `source_excerpt` from the document but a `value` invented out
  of thin air. New `value_anchor_failures` list surfaces these to the
  retry loop with a dedicated section in `templates/retry.j2` (v3.1.0)
  asking the model to re-quote or correct. Non-string values
  (numbers/booleans/null) skip the anchor check because validators
  legitimately normalise them away from excerpt surface forms.

### Added (Phase D — LLM-security input-side hardening)

- **Prompt-injection threat model** ([ADR-0011](docs/adr/0011-prompt-injection-threat-model.md))
  pins the trust boundary (caller trusted, document content untrusted) and
  documents the layered defences plus the explicit deferrals (vision
  grounding, MCP response sanitisation, semantic validity).
- **Document delimiter hardened.** `templates/extract.j2` (version 2.0.0)
  and `templates/retry.j2` (version 3.0.0) now wrap `document_text` in
  `<document>...</document>` XML tags instead of triple-quote `"""`. A
  document containing literal `"""` could escape the previous wrapper.
- **System prompt trust boundary.** `templates/system.j2` (version 1.1.0)
  carries an explicit paragraph telling the model that text inside
  `<document>` tags is untrusted user input and must be treated as data,
  not as instructions. Includes guidance on embedded `</document>`
  close-tags and on imperative language inside attached image pixels.
- **Vision-mode trust boundary.** `templates/extract_image.j2`
  (version 1.1.0) carries the same paragraph for image content,
  explicitly calling out adversarial pixel-level text.
- **Input cap.** `ExtractRequestBody.document_text` declares
  `max_length=200_000` (about 50K English tokens). Oversized payloads
  return HTTP 422 at the API layer with no Bedrock cost.
- New tests in `tests/test_prompts.py` pin the wrapper-tag contract and
  exercise an adversarial body containing `</document>` + "ignore
  previous instructions". New test in `tests/test_api.py` pins the 422
  on oversized `document_text`.

## [0.3.0] — 2026-05-09

### Added (Phase D — extraction surface, async, observability follow-on)

- **`POST /extract/stream`** — Server-Sent Events streaming endpoint. Emits
  `event: chunk` per text delta from `Agent.stream_async`, then exactly one
  terminal `event: result` (success) or `event: error` (parse/validation
  failure). Schema resolution is pre-flighted so unknown-name or
  mismatched-version requests return HTTP 404 *before* the SSE response
  opens. Retry loop intentionally bypassed (re-prompting mid-stream is poor
  UX); see ADR-0009.
- **Scanned-PDF rasterisation**: PDFs without embedded text are now
  rendered server-side via `pypdfium2` at 200 DPI (`RASTER_DPI`), capped at
  5 pages (`RASTER_MAX_PAGES`), and routed to the existing vision path.
  Closes the v0.3 deferral from ADR-0008. Mixed-text PDFs continue to use
  the text path; a WARNING log surfaces silently-dropped scanned pages.
- **Request-side schema versioning**: `ExtractRequestBody.schema_version`
  and the matching `Form` field on `/extract/document` pin the request to
  a registered schema version. Pin-or-fail: mismatch returns 404. Omitting
  tracks HEAD of the registry. Helper promoted to public
  `extraction.schemas.resolve_schema(name, version)`.
- **Non-blocking route handlers**: `/extract` and `/extract/document` now
  offload the synchronous `ExtractionService.extract` /
  `extract_document` calls via `asyncio.to_thread`, so the Bedrock
  round-trip no longer blocks the FastAPI event loop. The threadpool path
  unblocks today; native `Agent.invoke_async` is reserved for the
  streaming path.
- **OpenAPI typed-error coverage**: `/extract` documents 404 + 502 with
  `ErrorResponse`; `/extract/document` documents 404 + 422 + 502.
  Validation 422s on `/extract` continue to use FastAPI's default
  `HTTPValidationError` shape. Three new
  `test_openapi_documents_*` tests pin the contract.
- **Boto3-layer e2e test** (`tests/test_bedrock_boto3_e2e.py`): patches
  `BaseClient._make_api_call` to drive a real `BedrockModel` end-to-end
  offline, asserting `modelId` and request shape on the
  `bedrock-runtime` `ConverseStream` call. Catches `BedrockModel`-wiring
  regressions the higher-layer Agent mocks cannot see.
- **CLAUDE.md**: project-specific Claude Code context — editing gotchas,
  testing conventions (`stub_extraction_service`, the `_make_api_call`
  pattern, the PIL → image-only-PDF surrogate), and pre-defined project
  subagents (`prompt-template-reviewer`, `security-reviewer`).
- **ADRs 0005–0007 and 0009–0010** backfilled: Bedrock as default
  provider; JSON-only response contract (prompt-first, parser-tolerant);
  MCP-as-tools; bounded self-correcting retry (`max_retries=2`, ≤3 total
  attempts); ≥85% coverage gate.
- New telemetry attribute `extraction.streamed_chars` on the
  `extraction.stream` span; existing attributes mirrored from
  `extraction.run` so dashboards aggregating across modes don't drop
  streamed traffic.
- `pypdfium2>=4.30` added as a hard dep.
- `tests/conftest.py::stub_streaming_service` and
  `stub_streaming_service_returns_garbage` fixtures for streaming and
  error-path tests.

### Changed

- `extraction.service._resolve_schema` → public
  `extraction.schemas.resolve_schema`. The helper is a schema-lookup
  utility, not service state; promoting it eliminated the only
  cross-module private import.
- ADR-0009 supersedes the placeholder "retry-once-then-fail" entry on
  the previous roadmap; the actual default has been `max_retries=2`
  since v0.2 (3 total attempts at worst).
- Coverage gate raised 80 → 85 (actual ~93%).

### Added (Phase A — production hardening)

- Pre-commit gate (`.pre-commit-config.yaml`): ruff, ruff-format, mypy, bandit,
  gitleaks, eof/trailing-whitespace, yaml/toml validation. New `make hooks`
  target and a CI `pre-commit` job for parity with local.
- Secret scanning via gitleaks (`.gitleaks.toml`, `.github/workflows/gitleaks.yml`)
  with allowlists for `.env.example`, fixtures, and Bedrock model identifiers.
- SAST via Semgrep (`.semgrep.yml`, `.github/workflows/semgrep.yml`): community
  packs (`p/python`, `p/owasp-top-ten`, `p/jwt`, `p/secrets`) plus three
  project-specific rules (block `print(document_text)`, block f-string Jinja
  rendering, require Bedrock calls to be retry-wrapped).
- Renovate (`renovate.json5`) for `pep621` + `lockFileMaintenance` with the
  `uvLockFile` post-update hook. Dependabot trimmed to `github-actions` only
  (Dependabot's pip ecosystem cannot reconcile `uv.lock`).
- VS Code `.devcontainer` (Python 3.12, uv, aws-cli, gh, ruff/mypy/yaml
  extensions; `make install && make hooks` on create).
- ADR directory under `docs/adr/` with Nygard-format template, README index,
  and the first three records: Strands SDK choice, schema-first extraction
  with self-correcting retry, and the proposed AgentCore full-platform
  deployment.
- `docs/slos.md` documenting availability 99.9%/30 d, p95 < 3 s, error rate
  < 1%, retry rate < 5%, and SRE-workbook fast/slow burn alerts.
- Auth middleware (`api/auth.py`) with `none|apikey|jwt|both` modes; Cognito
  JWKS validation via `pyjwt[crypto]`; structured 401 carrying correlation id;
  configurable excluded paths for liveness probes.
- Rate limiting (`api/ratelimit.py`) via `slowapi` — per-API-key key function
  with IP fallback, `/extract`-only via route decorator, env-driven enable
  flag, defaults disabled to keep existing behaviour.
- Bedrock retry wrapper (`agent/bedrock_retry.py`) using `tenacity` with
  exponential-jitter backoff; retries `ThrottlingException`,
  `ServiceQuotaExceededException`, `ModelStreamErrorException`,
  `InternalServerException`, `ServiceUnavailableException`, and any 5xx
  `ClientError`. Emits a `bedrock.retry` event on the active span.
- k6 load-test scaffolding (`tests/load/`) with smoke (5 RPS / 2 m) and soak
  (50 RPS / 5 m) scenarios and `make load-test`.
- Runbooks under `docs/runbooks/`: on-call entry, Bedrock throttling,
  extraction-error spike, auth misconfig.
- mkdocs Material site (`mkdocs.yml`, `docs/index.md`, GitHub Pages
  deployment via tag-triggered workflow).
- **`POST /extract/document`** — multipart upload endpoint that auto-routes
  PDFs (with embedded text via `pypdf`) to the existing text path and
  images (PNG/JPEG/WebP/GIF) to a new Bedrock multimodal vision path.
  No separate OCR engine in the pipeline; vision calls go through
  `bedrock-runtime.converse` with image content blocks. Citation
  verification is disabled in vision mode (no canonical text). See
  [`docs/extraction-modes.md`](docs/extraction-modes.md) and
  [ADR-0008](docs/adr/0008-extraction-modes-text-vs-vision.md). Pulled
  forward from the v0.3 ROADMAP deferral.
- New modules: `extraction.document` (input detection),
  `extraction.multimodal` (Bedrock Converse direct call),
  `templates/extract_image.j2` (vision-mode prompt).
- US-only PII redaction module (`security.redaction`) — masks SSN, EIN,
  email, US phone, credit card, and AWS / API-key patterns. Used by
  the upcoming Phase D log redaction filter and span attribute writer.
- Citation verification module (`extraction.citations`) — `verify_excerpt`
  returns verbatim flag + best-overlap percentage via stdlib `difflib`.
- Validators module (`extraction.validators`) — single source of truth
  for SSN/EIN/email/date semantic checks. The agent-side `@tool` shims
  in `agent.tools` now delegate here.
- Weighted confidence calibration (`extraction.confidence`,
  [ADR-0004](docs/adr/0004-confidence-calibration.md)) — replaces the
  v0.1 simple-average overall confidence with required×2 / optional×1
  base weights plus ×1.2 validator-passed and ×1.1 citation-verified
  bonuses. Hard-zero on missing required fields.
- Multi-shot self-correcting retry: `max_retries` default 1 → 2; the
  retry prompt now carries three error sections (validation_errors /
  citation_failures / validator_failures) so the model can fix the
  *specific* issues. Each attempt emits its own `extraction.attempt`
  span with per-bucket counters.
- Prompt template versioning (`{# template_version: X.Y.Z #}` comment +
  `PromptRenderer.template_version`) so spans (Phase D1) and promptfoo
  (Phase D7) can A/B versions.

### Added

- Initial scaffold of `bedrock-strands-agent`: a form-extraction agent built on
  the Strands Agents SDK with Amazon Bedrock as the model provider.
- FastAPI HTTP service exposing `/health`, `/schemas`, `/extract`, and
  Prometheus `/metrics`.
- Pluggable schema registry with built-in IRS W-9 and invoice schemas.
- Strands tools for schema lookup, SSN/EIN/email validation, and date
  normalization.
- Declarative MCP integration via `mcp.config.json` and `MCP_ENABLED_SERVERS`.
- Jinja2 prompts (`system.j2`, `extract.j2`, `retry.j2`) with `StrictUndefined`.
- Self-correcting retry loop: if the model's first response fails schema
  validation, the service re-prompts once with `retry.j2` listing the errors.
- Structured JSON logging with X-Request-ID correlation propagation.
- OpenTelemetry tracing with optional OTLP, console, or no-op export modes;
  FastAPI and botocore instrumentors enabled.
- Multi-stage Dockerfile (uv builder, non-root runtime, OCI HEALTHCHECK) and
  docker-compose with otel-collector + Jaeger UI.
- GitHub Actions: lint, mypy strict, pytest matrix (3.12 + 3.13) with
  ≥80% coverage gate, bandit, pip-audit (against `uv export`-ed requirements),
  Trivy on the built image, CodeQL, and a tag-triggered release pipeline that
  publishes to GHCR with SBOM + provenance.
- `Makefile` with `check` (lint + typecheck + test + scan) and `ci` targets.
- `docs/ROADMAP.md` listing v0.2-and-later work intentionally deferred from
  the scaffold (auth, rate limiting, streaming, async agent, weighted
  confidence, ADRs).

### Changed (Phase A)

- Coverage gate raised from 80% → 85% (current coverage holds at ~91%).
- Dependabot scope reduced to `github-actions` only; Python and Docker
  ecosystems now managed by Renovate so `uv.lock` updates atomically with
  each PR.
- `_LEAKY_PREFIXES` in `tests/conftest.py` extended with `AUTH_*`, `JWT_*`,
  `API_KEYS`, `RATE_LIMIT_*` so the autouse env-isolation fixture covers
  the new settings.
- `_invoke` in `extraction/service.py` now routes Bedrock calls through
  `invoke_with_retry`, so transient throttling no longer surfaces as an
  immediate 502.
- `build_router` accepts `limiter` and `rate_limit` kwargs and registers
  `/extract` via `add_api_route` so the slowapi decorator can be applied
  conditionally without forking the route definition.

### Security (Phase A)

- Secret scanning enforced on every commit (pre-commit) and PR (CI).
- SAST via Semgrep complements existing bandit + CodeQL coverage with
  project-shaped rules.

### Changed

- Migrated from `[tool.uv.dev-dependencies]` to PEP 735 `[dependency-groups]`
  (the `uv` field was deprecated for removal).
- `make audit` audits the live venv with `--skip-editable` rather than an
  exported requirements file. The original `-r requirements.txt` form
  triggered pip-audit's internal venv creation, which fails to bootstrap
  `ensurepip` on uv-managed Python builds.

[Unreleased]: https://github.com/Grimm07/bedrock-strands-extraction-agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/Grimm07/bedrock-strands-extraction-agent/compare/v0.1.0...v0.3.0
