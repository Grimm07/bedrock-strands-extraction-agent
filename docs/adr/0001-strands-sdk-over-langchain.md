# ADR-0001: Strands SDK over LangChain / LlamaIndex

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The agent runtime needs:

- **Native Bedrock support** without writing a custom converse-API client and
  re-implementing tool-use plumbing.
- **First-class MCP** so we can plug in third-party tool servers without
  in-tree adapters.
- **Sync-friendly** call semantics. The service is fronted by FastAPI and
  blocks on a single Bedrock invocation per request — we did not want to pull
  in an event-loop-only runtime.
- **Strict Pydantic boundaries** — the extraction contract is JSON-shape-critical
  and the SDK must not silently forward malformed results.
- **Lightweight surface** — no compile-step graph (LCEL), no chain-of-runnables
  abstraction; runbooks should read like Python, not graph definitions.

LangChain (and LangGraph) and LlamaIndex were both evaluated. LangChain has the
broadest community but pulls a deep transitive set, encourages chain-style
composition that hides where Bedrock errors land, and its tool decorators are
not ergonomic with Pydantic strict mode. LlamaIndex is RAG-first, which is
orthogonal to our use case (form extraction, not retrieval-augmented Q&A).

## Decision

Use the Strands Agents SDK (`strands-agents`, `strands-agents-tools`) as the
agent runtime. Wrap Bedrock via `BedrockModel`. Register tools as plain
docstring-annotated functions through `@tool`. Preserve the FastAPI sync API.

## Consequences

**Positive**

- Smaller dependency surface; predictable upgrade cadence.
- Native MCP via `MCPManager` (see `src/bedrock_strands_agent/agent/mcp.py`).
- Strands' `AgentResult.metrics` exposes accumulated token counts and tool
  metrics directly — no need to scrape the raw Bedrock response, which
  simplifies Phase D telemetry.
- Strands hooks (`BeforeInvocationEvent`, `AfterToolCallEvent`, etc.) give a
  clean seam for telemetry without polluting the service layer.

**Negative**

- Smaller community than LangChain — fewer Stack Overflow answers, more
  reliance on official docs and reading source.
- Strands API is younger; we accept minor-version drift (e.g. metric key
  renames) and pin the dependency tightly.
- Some abstractions (e.g. `structured_output_model=` for Pydantic-enforced
  output, ADR-0004) are still evolving; we keep a JSON-fence fallback parser
  in `extraction/service.py` as a safety net.

## References

- `src/bedrock_strands_agent/agent/builder.py` — agent construction.
- `src/bedrock_strands_agent/agent/tools.py` — tool registration pattern.
- ADR-0002 — schema-first extraction with self-correcting retry.
- ADR-0003 — AgentCore full-platform deployment.
