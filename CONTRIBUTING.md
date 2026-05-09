# Contributing

The canonical contributing guide lives at
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md). It covers:

- Local dev setup (`make install`, `.env.example` → `.env`, `make dev`).
- The `make check` quality gate (`ruff`, `mypy`, `pytest --cov-fail-under=85`,
  `bandit`).
- The pre-commit code-quality review rule and project-defined subagents
  (`prompt-template-reviewer`, `security-reviewer`).
- Repo conventions (logging, schema lookup via `resolve_schema`, Bedrock
  retry wrapping).
- Conventional Commits + the release flow.

For repo orientation see [`README.md`](README.md); for what's intentionally
not yet shipped see [`docs/ROADMAP.md`](docs/ROADMAP.md).
