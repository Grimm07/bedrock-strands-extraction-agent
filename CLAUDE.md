# bedrock-strands-agent

FastAPI + Strands Agents extraction service over Amazon Bedrock. See
`docs/` (ADRs, runbooks, `extraction-modes.md`) for design context;
this file captures things that aren't obvious from the code.

## Commands

- `uv run pytest` — full suite (coverage gate 85%, set in `pyproject.toml`)
- `uv run ruff check src tests` — lint (rule set declared in `pyproject.toml`)
- `uv run python -m bedrock_strands_agent serve --reload` — local dev server
- `uv add <pkg>` — add deps; do NOT edit `uv.lock` directly (hook blocks it)
- `RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_bedrock_integration.py` —
  live-Bedrock test (requires AWS creds)

## Editing gotchas

- `.claude/hooks/ruff_format.sh` runs `ruff check --fix` after every
  Python Edit/Write, which auto-deletes unused imports (`F401`). Add
  the import + first usage in the *same* tool call, or use `Write` to
  rewrite the file in one shot — otherwise the import is gone before
  the next edit references it.
- `.claude/hooks/block_protected_files.sh` denies direct edits to
  `uv.lock` and `.env*` (`.env.example` is allowed). Use `uv add` via
  Bash; the hook only fires on the Edit/Write tools.

## Testing conventions

- `tests/conftest.py::stub_extraction_service` mocks at the Strands
  `Agent.__call__` layer — use it for FastAPI route/service tests so
  they never touch Bedrock.
- For `BedrockModel`-wiring regressions, patch
  `botocore.client.BaseClient._make_api_call`. `botocore.stub.Stubber`
  cannot mock event-streams, and `BedrockModel` defaults to
  `ConverseStream` (streaming), not `Converse`. See
  `tests/test_bedrock_boto3_e2e.py` for the canonical pattern.
- Scanned-PDF surrogate: `PIL.Image.save(buf, format="PDF")` writes an
  image-only PDF whose `pypdf.extract_text()` returns `""` for every
  page. No binary fixtures committed; see
  `tests/test_document.py::_image_only_pdf_bytes`.

## Project subagents (in `.claude/agents/`)

- `prompt-template-reviewer` — Jinja invariants on
  `src/bedrock_strands_agent/templates/*.j2`.
- `security-reviewer` — PII / secret / span-attribute hygiene. Fire it
  when touching `extraction/service.py`, `api/routes.py`, `logging.py`,
  or `telemetry.py`.

For pre-merge code review use `pr-review-toolkit:code-reviewer`.
