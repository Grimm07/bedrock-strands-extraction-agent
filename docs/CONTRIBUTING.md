# Contributing

Thank you for your interest in `bedrock-strands-agent`!

## Development setup

```bash
# Python 3.12+ and uv required.
make install                  # creates .venv, installs deps + dev deps
cp .env.example .env          # then edit AWS_REGION + BEDROCK_MODEL_ID
make hooks                    # install pre-commit + commit-msg hooks
make dev                      # uvicorn with auto-reload at :8000
```

`AUTH_MODE` defaults to `none` for local dev. Set
`MCP_ENABLED_SERVERS=filesystem,fetch` (or whatever subset of
`mcp.config.json`) to load MCP tools alongside the built-in ones.

For the local observability stack:

```bash
docker compose -f docker/docker-compose.yml up
# Jaeger UI: http://localhost:16686
```

## Quality gate

Run the full gate before opening a pull request:

```bash
make check
```

This runs:

1. `ruff check` (lint)
2. `ruff format --check` (formatting)
3. `mypy` (strict type checking)
4. `pytest` with `--cov-fail-under=85`
5. `bandit` (security scan)

CI runs the same plus `pip-audit` against an exported lockfile, Trivy
against the built container image, and semgrep with project-specific rules
(see `.semgrep.yml`).

## Code review before commit

This repo's house rule: **every batch of changes goes through a code-quality
review before the commit lands**, including changes implemented by the
orchestrator itself. Use the `pr-review-toolkit:code-reviewer` agent (or an
equivalent independent reviewer). Address `NEEDS_CHANGES` findings before
committing; document any deliberately-deferred nits in the commit message.

For changes that touch prompt templates (`src/bedrock_strands_agent/templates/*.j2`)
or anything that could affect PII handling (`extraction/service.py`,
`api/routes.py`, `logging.py`, `telemetry.py`), also fire the
project-defined `prompt-template-reviewer` and `security-reviewer` subagents
in `.claude/agents/`.

## Conventions

- **Python 3.12+**. Code is type-checked under mypy strict.
- **Docstrings**: Google style, enforced by ruff `D` rules. Source files are
  linted under `E W F I B C4 UP SIM RUF S D ANN ASYNC PT RET TRY PERF`;
  `tests/**/*.py` ignores `D S ANN PT011 PLR2004`.
- **Pydantic v2** for data models; `extra="forbid"` on request models.
- **Logging**: structured JSON via the configured logger, never `print()`.
  Never log `document_text` or `ExtractedField.value` — semgrep enforces this.
- **No new mocks of AWS** at the Strands level for *new* test patterns —
  pass a stub `ExtractionService` into `create_app` instead. See
  `tests/conftest.py::stub_extraction_service`. For `BedrockModel`-wiring
  tests, follow `tests/test_bedrock_boto3_e2e.py` (patches
  `BaseClient._make_api_call`).
- **Schema lookup**: use `extraction.schemas.resolve_schema(name, version)`
  for any new entry point that resolves a schema; do not re-roll the
  pin-or-fail check.
- **Bedrock retries**: any new Bedrock-touching callable must be wrapped in
  `agent.bedrock_retry.invoke_with_retry`. The semgrep rule
  `bedrock-call-must-be-retry-wrapped` enforces this.

## Commit style

Conventional Commits, e.g.:

- `feat(api): add /healthz alias`
- `fix(extraction): handle bare-array model output`
- `chore(deps): bump strands-agents to 0.2.0`

The `release-notes` skill (`/release-notes`) drafts the next CHANGELOG
section from `git log v$prev..HEAD`, grouping by Conventional Commits prefix.

## Releasing

1. Bump `pyproject.toml`'s `version` field.
2. Move the `[Unreleased]` section in `CHANGELOG.md` to `[X.Y.Z] — YYYY-MM-DD`.
3. Commit, then tag: `git tag vX.Y.Z && git push --tags`.
4. The release workflow builds and pushes a signed container image with
   SBOM and SLSA provenance to GHCR, runs Trivy, and creates a GitHub Release.
