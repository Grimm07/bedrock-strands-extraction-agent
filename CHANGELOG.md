# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/your-org/bedrock-strands-agent/compare/HEAD
