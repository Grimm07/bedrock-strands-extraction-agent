# Contributing

Thank you for your interest in `bedrock-strands-agent`!

## Development setup

```bash
make install
cp .env.example .env
make dev
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
4. `pytest` with `--cov-fail-under=80`
5. `bandit` (security scan)

CI runs the same plus `pip-audit` against an exported lockfile and Trivy
against the built container image.

## Conventions

- **Python 3.12+**. Code is type-checked under mypy strict.
- **Docstrings**: Google style, enforced by ruff `D` rules.
- **Pydantic v2** for data models; `extra="forbid"` on request models.
- **Logging**: structured JSON via the configured logger, never `print()`.
- **No new mocks of AWS** in tests — pass a stub `ExtractionService` into
  `create_app` instead. See `tests/conftest.py::stub_extraction_service`.

## Commit style

Conventional Commits, e.g.:

- `feat(api): add /healthz alias`
- `fix(extraction): handle bare-array model output`
- `chore(deps): bump strands-agents to 0.2.0`

## Releasing

Tag the commit (`vX.Y.Z`). The release workflow builds and pushes a signed
container image with SBOM and provenance to GHCR, runs Trivy, and creates a
GitHub Release.
