# ADR-0007: MCP as the tool extension boundary

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The base service does schema-first form extraction against a small, fixed set
of built-in tools. In practice extraction quality often turns on lookups the
service has no business shipping in its container:

- A vendor master from the customer's ERP, used to canonicalise supplier names
  on an invoice ("ACME Corp." vs. "Acme Corporation Ltd.").
- Address validation against a national post-code or USPS-equivalent service.
- Currency conversion against the rate source the customer's finance team has
  already audited.
- Organisation-specific KYC / sanction-list checks that the customer's
  compliance team owns and refuses to externalise.

These are all tenant-specific. None of them belong in the base image: their
credentials, their data and their licensing are owned by the customer. We
therefore need a **tool extension boundary** that:

1. Lets a customer drop in their own tool implementation without forking the
   service or shipping a custom container.
2. Keeps tool surface explicit so ops can audit what's wired in for any given
   deployment.
3. Imposes near-zero per-call latency, because a single extraction can fan out
   to multiple lookups inside one model turn.
4. Doesn't multiply the supply-chain review surface — every extra plugin
   format is another thing for `security-reviewer` to reason about.

The Model Context Protocol (MCP) is the industry-standard answer to exactly
this problem: a JSON-RPC tool protocol over stdio (and, optionally, SSE/HTTP),
already supported first-party by Strands via `strands.tools.mcp.MCPClient`.

## Decision

Adopt **MCP** as the sole tool extension mechanism. Concretely:

- Servers are declared in **`mcp.config.json`** at the project root, in the
  same `mcpServers` shape every other MCP host uses (Claude Desktop, Claude
  Code, Cursor). This is portable: a customer's existing MCP servers drop in
  without translation.
- An **enabled-server allowlist** (`MCP_ENABLED_SERVERS`, exposed in
  `bedrock_strands_agent.config.Settings`) gates which entries actually start.
  Presence in `mcp.config.json` is necessary but not sufficient.
- **`MCPManager`** (`src/bedrock_strands_agent/agent/mcp.py`) owns the
  lifecycle: at app startup it loads the config, validates each enabled
  entry, spawns the server as a stdio subprocess via
  `mcp.client.stdio.stdio_client`, wraps it in a `strands.tools.mcp.MCPClient`,
  and calls `list_tools_sync()` once. The resulting tools are appended to the
  built-in tool list when the Strands `Agent` is assembled in
  `agent/builder.py`.
- An `ExitStack` holds every spawned client; FastAPI lifespan shutdown calls
  `MCPManager.stop()`, which closes the stack and reaps subprocesses
  deterministically.

**Alternatives considered:**

- **A custom Python plugin system** — e.g. an `entry_points` group, each
  customer shipping a wheel that registers tools. Rejected: every customer
  carries a Python dependency on our internals; the ABI churns whenever we
  bump Strands; security review has to read each plugin's source; we would
  effectively be reinventing the MCP spec at lower quality, with none of the
  ecosystem.
- **A sidecar HTTP service per tool**, called over loopback. Rejected: each
  call pays a connection + TLS + auth round-trip, multiplied by every tool a
  model uses inside one extraction turn. MCP's stdio transport is
  process-local and an order of magnitude cheaper for the short-lived,
  high-fan-out call pattern an extraction agent generates. Sidecars also
  duplicate observability (separate logs, separate tracing, separate IAM)
  for no benefit.
- **No external tools — close the surface entirely.** Rejected: known
  customer asks already require lookups we cannot reasonably ship in the
  base service (vendor masters, ERP IDs, internal KYC). Refusing a
  tool boundary just pushes customers to fork.

## Consequences

**Positive**

- The Strands `Agent` treats MCP-provided tools identically to built-in ones
  — same JSON-schema description, same tool-use loop. The model has no way to
  tell them apart, so prompt engineering against built-ins generalises to
  customer tools for free.
- Subprocess startup cost is paid **once per worker**, not per request:
  `MCPManager.start()` runs synchronously inside `build_agent()` during
  `create_app()` (`src/bedrock_strands_agent/agent/builder.py:57`),
  holds the stdio session open, and reuses it for every `Agent`
  invocation in that worker. The FastAPI lifespan owns shutdown only
  (`api/app.py:64-68`), tearing the session down on worker exit.
- The allowlist gives ops a hard kill-switch: a misconfigured
  `mcp.config.json` checked into the repo cannot ship tools that
  `MCP_ENABLED_SERVERS` hasn't named. Forgetting to set the env var means
  zero tools, not all tools.
- Server-side validators (`extraction.validators`) run on every extraction
  output regardless of which tool produced an intermediate value, so a
  misbehaving MCP tool cannot bypass the schema contract — it can only
  degrade quality, not break the API.
- If MCP fragments, or a customer demands a non-standard transport (SSE,
  HTTP, named pipes), we layer adaptors inside `MCPManager._build_client`
  without touching `agent/builder.py` or the FastAPI routes.

**Negative**

- Each enabled MCP server is a subprocess that **inherits the worker's
  process identity**. Customers must trust the binaries they enable;
  there's no in-container sandbox today. The `.claude/agents/security-reviewer`
  subagent flags any change to `mcp.config.json` or `MCP_ENABLED_SERVERS`
  for review, and runbook guidance is to enable only servers whose source or
  vendor has been through the same supply-chain check we apply to base-image
  dependencies.
- `mcp.config.json` is project-local. Multi-tenant deployments where
  different tenants want different tool sets are not yet supported — every
  worker today sees the same enabled set. ADR follow-up will revisit this if
  AgentCore's per-tenant routing (ADR-0003) lands before we have a customer
  asking.
- Stdio transport assumes the server can run inside the worker container.
  Network-only tools (a SaaS API the customer hosts) require either an MCP
  shim that wraps the API, or eventual SSE-transport support; the latter is
  flagged as future work in `MCPManager._build_client`, which currently
  raises `MCPConfigError` on non-stdio transports rather than silently
  doing the wrong thing.
- We are betting on MCP's longevity. The mitigation is that MCP is now the
  default tool boundary in Strands, Claude Desktop, Claude Code and Cursor;
  fragmentation looks unlikely on the timescale of this service.

## References

- ADR-0001 — Strands SDK selection (`strands.tools.mcp.MCPClient` is the
  upstream integration this ADR depends on).
- `src/bedrock_strands_agent/agent/mcp.py` — `MCPManager`, config loader,
  stdio-client construction, tool surfacing.
- `src/bedrock_strands_agent/agent/builder.py` — Agent assembly that
  concatenates built-in tools with `MCPManager.tools`.
- `mcp.config.json` — project-local server definitions (filesystem, fetch
  shipped as defaults; not enabled unless named in `MCP_ENABLED_SERVERS`).
- `src/bedrock_strands_agent/config.py` — `MCP_ENABLED_SERVERS` setting,
  consumed by the FastAPI lifespan to construct `MCPManager`.
- `.claude/agents/security-reviewer.md` — subagent that flags MCP additions
  for review.
