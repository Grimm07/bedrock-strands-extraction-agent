# bedrock-strands-agent — Agent Onboarding

This is the canonical project-context file read by tools that follow the
[agents.md](https://agents.md/) spec — currently **opencode** (via
`opencode.json::instructions`) and **Cursor** (auto-detected). Claude
Code reads its own `CLAUDE.md` for Claude-specific quirks; the
substantive overlap with this file is intentional.

## What this service is

FastAPI + Strands Agents extraction service over Amazon Bedrock. The
hot path: PDF / image / text → schema-validated JSON with
field-level confidence and citation excerpts. See `docs/` (ADRs,
runbooks, `extraction-modes.md`) for design context.

## Commands

- `uv run pytest` — full suite (coverage gate 85%, set in `pyproject.toml`)
- `uv run ruff check src tests` — lint (rule set declared in `pyproject.toml`)
- `uv run mypy src` — strict typecheck
- `uv run python -m bedrock_strands_agent serve --reload` — local dev server
- `uv add <pkg>` — add deps; do NOT edit `uv.lock` directly
- `RUN_INTEGRATION_TESTS=1 uv run pytest tests/test_bedrock_integration.py` —
  live-Bedrock test (requires AWS creds)

After every Python edit, run `uv run ruff format <file>` and
`uv run ruff check --fix <file>` — Claude Code's hook does this
automatically; opencode and Cursor users do it manually.

## Branch model

`main` and `dev` are both **protected** — direct push is forbidden.
Every change must land via merge/pull request from a feature branch
(`feat/...`, `fix/...`, `chore/...`). Standard target is `dev`;
release-level merges from `dev` go to `main`.

## Files you must NOT edit directly

- `uv.lock` — managed by `uv add` / `uv lock`; manual edits diverge the
  lockfile from the resolver and break reproducibility.
- `.env`, `.env.local`, `.env.<env>`, `.env.<env>.local` — these
  contain secrets. `.env.example` is the only `.env*` file that is
  intentionally checked in and editable.

Claude Code blocks these via a hook; opencode and Cursor users have to
honour the rule manually.

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

## Sensitive-data discipline (defence in depth)

This service handles PII (SSN / EIN / tax-form data). Two invariants
are non-negotiable:

1. **Never log or span-attribute `document_text` or `ExtractedField.value`.**
   The allowed span-attribute set is metadata only: `extraction.schema`,
   `extraction.schema_version`, `bedrock.model_id`,
   `extraction.document_id`, `extraction.correlation_id`,
   `extraction.latency_ms`, `extraction.field_count`,
   `extraction.warning_count`, `extraction.retry_attempt`.
2. **Never echo input in error messages.** `ExtractionError(f"...
   {raw}")` where `raw` could contain document text is a leak. Raise
   generic messages.

The CI semgrep rule `no-print-of-document-text` enforces #1 mechanically
for the `print` / `LOGGER` direct-format case. `extra={"foo":
document_text}` indirection is on the developer to avoid.

## Project subagents (Claude Code) and equivalents

| Concern | Claude subagent | OpenCode subagent | Cursor rule |
|---|---|---|---|
| PII / auth / secrets / span-attribute hygiene | `.claude/agents/security-reviewer.md` | `.opencode/agent/security-reviewer.md` | `.cursor/rules/10-security-pii.mdc` |
| Jinja template invariants (JSON contract, StrictUndefined safety) | `.claude/agents/prompt-template-reviewer.md` | `.opencode/agent/prompt-template-reviewer.md` | `.cursor/rules/20-prompt-templates.mdc` |
| Drafting CHANGELOG from `git log` | `.claude/skills/release-notes/SKILL.md` | `.opencode/command/release-notes.md` | (manual — see release-notes section in this file) |

Fire the security reviewer when touching `extraction/service.py`,
`api/routes.py`, `logging.py`, or `telemetry.py`. Fire the
prompt-template reviewer when touching anything under
`src/bedrock_strands_agent/templates/`.

## MCP servers wired for AI-assistant use

Two MCP servers are configured for Claude (`.mcp.json`), opencode
(`opencode.json::mcp`), and Cursor (`.cursor/mcp.json`):

- **`context7`** — live documentation lookup for Strands Agents SDK,
  pydantic-settings, FastAPI, OpenTelemetry. No API key required;
  `CONTEXT7_API_KEY` raises rate limits.
- **`awslabs.aws-api-mcp-server`** — Bedrock model listing, IAM,
  CloudWatch read access. Honors the standard boto3 credential chain
  (`AWS_PROFILE`, `AWS_REGION`, `AWS_API_MCP_PROFILE_NAME`). Use a
  read-only profile when exploring.

There is also a **separate** MCP config — `mcp.config.json` — that is
the **Strands SDK runtime** MCP config (filesystem + fetch tools the
agent itself can call). Don't confuse the two: `.mcp.json` /
`opencode.json` / `.cursor/mcp.json` are for the AI assistant;
`mcp.config.json` is for the running Strands agent.

## Release flow

CHANGELOG follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
commits follow Conventional Commits. The release workflow at
`.github/workflows/release.yml` is tag-triggered; for the procedure see
`.claude/skills/release-notes/SKILL.md` or `.opencode/command/release-notes.md`.
Version bump rules: `feat!:` → major, `feat:` → minor, `fix:` /
`chore:` / `refactor:` / `perf:` → patch.

## Where to find more

- **ADRs** — `docs/adr/` (17 ADRs covering Strands SDK choice, schema
  versioning, vision grounding, retry strategy, observability).
- **Runbooks** — `docs/runbooks/` (oncall, Bedrock throttling,
  extraction-error spike, auth misconfig).
- **Roadmap** — `docs/ROADMAP.md`. Most-recent automation pass log:
  `.claude/AUTOMATION_ROADMAP.md`.
