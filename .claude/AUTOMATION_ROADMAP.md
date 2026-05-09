# Claude Code Automation Roadmap

Tracks Claude Code automations (hooks, skills, subagents, MCP servers) that are
planned for this repo but not yet shipped. Order is roughly priority-by-payoff.

## Shipped

- **Hook: ruff format-on-edit** — `.claude/hooks/ruff_format.sh`. Runs
  `uv run ruff format` then `uv run ruff check --fix` on any `.py` file
  Claude edits.
- **Hook: protected-file blocker** — `.claude/hooks/block_protected_files.sh`.
  Blocks direct edits to `uv.lock` and `.env*` (allows `.env.example`).

## Next up

### MCP servers

- **`context7`** — live documentation for Strands Agents SDK,
  pydantic-settings v2, OpenTelemetry instrumentors, MCP `StdioServerParameters`,
  FastAPI. Training-data answers go stale fast for these.
  - Install: `claude mcp add context7`
  - Commit `.mcp.json` + add `enableAllProjectMcpServers: true` (or list it in
    `enabledMcpjsonServers`) in `.claude/settings.json` so the team picks it up.

- **AWS MCP server** — verify Bedrock model availability in
  `${AWS_REGION}`, check `bedrock:InvokeModel` IAM perms, inspect CloudWatch
  traces, without paying the context cost of pasting `aws` CLI output.
  - Pick a flavor: `awslabs/mcp` has multiple. The Bedrock-focused one is
    closest to what this service needs.

### Skills

- **`add-form-schema`** *(custom, user-only)* — scaffold a new `FormSchema`:
  define `FieldDefinition`s, register in `examples.py` (or via
  `register_schema`), update README's "Adding a schema" section, add a
  parametrized extraction test. Path: `.claude/skills/add-form-schema/SKILL.md`.
  Bundle templates for common forms (1099, K-1, packing slip).
  Frontmatter: `disable-model-invocation: true` (it writes files).

- **`release-notes`** *(custom)* — draft the next `[Unreleased]` →
  `[X.Y.Z]` `CHANGELOG.md` section from `git log v$prev..HEAD`. Pairs with the
  tag-triggered GHCR release workflow already in `.github/workflows/release.yml`.
  Path: `.claude/skills/release-notes/SKILL.md`. Both-invocable.

### Subagents

- **`prompt-template-reviewer`** *(custom)* — fires on changes to
  `src/bedrock_strands_agent/templates/*.j2`. Checks: (1) JSON shape contract
  preserved, (2) `StrictUndefined`-safe (no shadowed variables), (3) document
  and schema both still injected, (4) retry template still includes `errors`,
  (5) the no-fences instruction in `system.j2` still present (parser depends
  on it). Path: `.claude/agents/prompt-template-reviewer.md`.

- **`security-reviewer`** — checks for: logging of raw `document_text`,
  missing redaction in error messages and OTel span attributes, raw model
  output in logs, missing auth boundary documentation. The service handles
  SSN/EIN/tax data — generic security review under-prioritizes this.
  Path: `.claude/agents/security-reviewer.md`.

### Plugins

- **`mcp-builder`** *(only when relevant)* — if/when we ship a domain-specific
  MCP server that exposes the schema registry and validators to *other*
  Strands agents, mcp-builder is the right scaffold. Skip until that need
  exists.

## Implementation order suggestion

1. `context7` MCP — biggest immediate payoff, near-zero risk.
2. `prompt-template-reviewer` subagent — protects the most fragile and
   highest-blast-radius part of the codebase.
3. `add-form-schema` skill — eliminates the most common manual stamping.
4. `security-reviewer` subagent — once auth is even partially implemented
   (currently flagged in `docs/ROADMAP.md`).
5. AWS MCP — useful but most often replaceable by direct `aws` CLI use.
6. `release-notes` skill — nice-to-have, low frequency.
7. `mcp-builder` — only when there's an MCP server to build.

## Notes for implementers

- **Hook scoping**: project-level hooks are picked up only when `claude` is
  started from inside the repo (or a subdirectory). After cloning, run
  `/hooks` once to confirm Claude has loaded `.claude/settings.json`.
- **Skill invocation control**:
  - `disable-model-invocation: true` — user-only (use for skills with side
    effects: deploy, commit, write files).
  - `user-invocable: false` — Claude-only (background knowledge, conventions).
  - Default — both.
- **MCP servers committed at the repo level**: prefer `.mcp.json` in the repo
  root + `enableAllProjectMcpServers: true` in `.claude/settings.json` so the
  whole team gets the same servers.
- **Sensitive data**: never let `document_text` (the raw form text) hit a
  logged span attribute. The `security-reviewer` subagent should enforce
  this when added.
