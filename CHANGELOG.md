# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

### Changed

- Migrated from `[tool.uv.dev-dependencies]` to PEP 735 `[dependency-groups]`
  (the `uv` field was deprecated for removal).
- `make audit` audits the live venv with `--skip-editable` rather than an
  exported requirements file. The original `-r requirements.txt` form
  triggered pip-audit's internal venv creation, which fails to bootstrap
  `ensurepip` on uv-managed Python builds.

[Unreleased]: https://github.com/your-org/bedrock-strands-agent/compare/HEAD
