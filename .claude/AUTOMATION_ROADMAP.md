# Claude Code Automation Roadmap

Tracks Claude Code automations (hooks, skills, subagents, MCP servers) for
this repo. Items move from **Next up** to **Shipped** as they land.

## Shipped

### Hooks

- **ruff format-on-edit** — `.claude/hooks/ruff_format.sh`. Runs
  `uv run ruff format` then `uv run ruff check --fix` on any `.py` file
  Claude edits. Best-effort, silent, never blocks.
- **Protected-file blocker** — `.claude/hooks/block_protected_files.sh`.
  Denies direct edits to `uv.lock` and `.env*` (allows `.env.example`)
  using the modern `hookSpecificOutput.permissionDecision` protocol with a
  clear reason.

### MCP servers (for Claude Code, distinct from `mcp.config.json` which is
the Strands SDK runtime config)

- **`context7`** (`@upstash/context7-mcp` via `npx -y`) — live documentation
  lookup for Strands Agents SDK, pydantic-settings v2, FastAPI, OpenTelemetry.
  No API key required for anonymous use; set `CONTEXT7_API_KEY` for higher
  rate limits.
- **`awslabs.aws-api-mcp-server`** (PyPI, via `uvx ...@latest`) — unified
  AWS API access for Bedrock model listing, IAM, and CloudWatch. Honors the
  standard boto3 credential chain (`AWS_PROFILE`, `AWS_REGION`,
  `AWS_API_MCP_PROFILE_NAME`). Use a read-only profile when exploring.

Both are auto-enabled for the team via
`enabledMcpjsonServers: ["context7", "awslabs.aws-api-mcp-server"]` in
`.claude/settings.json`.

### Skills

- **`add-form-schema`** *(user-only)* — `.claude/skills/add-form-schema/`.
  Walks through scaffolding a new `FormSchema`: gather field specs, append
  to `examples.py`, register, write a parametrized test, update README and
  CHANGELOG. Bundles ready-to-use templates for IRS 1099-MISC and K-1.
  `disable-model-invocation: true` because it writes new files.
- **`release-notes`** *(both)* — `.claude/skills/release-notes/`. Drafts the
  next `[X.Y.Z]` `CHANGELOG.md` section from `git log v$prev..HEAD`,
  groups by Conventional Commits prefix into Keep-a-Changelog buckets,
  folds dependabot bumps into a single line, proposes the semver bump,
  and shows the tag step that triggers the GHCR release workflow.

### Subagents

- **`prompt-template-reviewer`** — `.claude/agents/prompt-template-reviewer.md`.
  Read-only review of `src/bedrock_strands_agent/templates/*.j2` against
  six invariants (JSON contract, no-fences instruction, StrictUndefined
  safety, field-attribute injection, retry-prompt errors+document
  injection, PII templating). Tools: `Read`, `Grep`, `Glob`.
- **`security-reviewer`** — `.claude/agents/security-reviewer.md`. Read-only
  review for PII leakage in this extraction service: logs/spans containing
  `document_text` or `ExtractedField.value`, error messages echoing input,
  routes added without auth, span attribute names that imply PII, hardcoded
  secrets, prod-env exporters/log levels. Includes 3 fast-pass `rg`
  triage commands. Tools: `Read`, `Grep`, `Glob`, `Bash`.

## Next up

### Plugins (conditional)

- **`mcp-builder`** *(only when relevant)* — if/when we ship a domain-specific
  MCP server that exposes the schema registry and validators to *other*
  Strands agents, mcp-builder is the right scaffold. Skip until that need
  exists.

## Notes for users

- **Hook scoping**: project-level hooks/MCP servers are picked up only when
  `claude` is started from inside this repo (or a subdirectory). After
  pulling new automations, run `/hooks` once to confirm Claude has loaded
  `.claude/settings.json`. The first time you use an MCP server you'll be
  prompted to approve it.
- **Skill invocation control**:
  - `disable-model-invocation: true` — user-only (used here for
    `add-form-schema` because it writes new files).
  - `user-invocable: false` — Claude-only (background knowledge,
    conventions). Not used yet.
  - Default — both can invoke. Used here for `release-notes`.
- **Sensitive data**: never let `document_text` (the raw form text) hit a
  logged span attribute. The `security-reviewer` subagent enforces this on
  every PR — fire it explicitly when touching `extraction/service.py`,
  `api/routes.py`, `logging.py`, or `telemetry.py`.

## Activating MCP servers

The two project MCP servers need their respective tooling on PATH and (in
the AWS case) credentials in the developer's shell:

```bash
# context7 (no creds needed; npx is bundled with Node 18+)
npx --version

# AWS MCP server
uvx --version       # comes with uv
aws sts get-caller-identity   # confirms creds + region resolve
export AWS_PROFILE=...        # optional; defaults to "default"
```

When you first open Claude Code in this repo with both servers configured,
you'll see two approval prompts. Approve them once and they're sticky.
