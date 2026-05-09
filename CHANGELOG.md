# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/your-org/bedrock-strands-agent/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/your-org/bedrock-strands-agent/compare/v0.1.0...v0.3.0
