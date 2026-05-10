# Architecture Decision Records

This directory captures architecturally significant decisions in
[Michael Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).
Copy `template.md` to start a new ADR.

| #    | Title                                                                                                               | Status   |
| ---- | ------------------------------------------------------------------------------------------------------------------- | -------- |
| [0001](./0001-strands-sdk-over-langchain.md)                                  | Strands SDK over LangChain/LlamaIndex                  | Accepted |
| [0002](./0002-schema-first-extraction-with-self-correcting-retry.md)          | Schema-first extraction with self-correcting retry     | Accepted |
| [0003](./0003-agentcore-full-platform-deployment.md)                          | AgentCore full-platform deployment                     | Proposed |
| [0004](./0004-confidence-calibration.md)                                      | Weighted overall-confidence calibration                | Accepted |
| [0005](./0005-bedrock-as-default-provider.md)                                 | Amazon Bedrock as the default model provider           | Accepted |
| [0006](./0006-json-only-response-contract.md)                                 | JSON-only response contract (prompt-first, parser-tolerant) | Accepted |
| [0007](./0007-mcp-as-tools.md)                                                | MCP-as-tools                                           | Accepted |
| [0008](./0008-extraction-modes-text-vs-vision.md)                             | Two extraction paths — text vs Bedrock vision (no separate OCR) | Accepted |
| [0009](./0009-bounded-self-correcting-retry.md)                               | Bounded self-correcting retry (max two re-prompts)     | Accepted |
| [0010](./0010-coverage-gate-85.md)                                            | ≥85% coverage gate                                     | Accepted |
| [0011](./0011-prompt-injection-threat-model.md)                               | Prompt-injection threat model and layered defences     | Accepted |
| [0012](./0012-a2a-protocol.md)                                                | A2A (agent-to-agent) protocol                          | Accepted |
| [0013](./0013-opentelemetry-observability-strategy.md)                        | OpenTelemetry-first observability with metadata-only spans | Accepted |
| [0014](./0014-streaming-retry-bypass.md)                                      | `/extract/stream` bypasses the self-correcting retry loop | Accepted |
| [0015](./0015-asyncio-to-thread-offload.md)                                   | `asyncio.to_thread` offload at the route boundary      | Accepted |
| [0016](./0016-vision-grounding-second-pass.md)                                | Vision-mode grounding via a second model call          | Accepted |
| [0017](./0017-two-layer-test-mocking-strategy.md)                             | Two-layer test mocking — Strands Agent + boto3         | Accepted |

## Conventions

- Filename: `NNNN-kebab-case-title.md`. Numbers are monotonic; never reuse.
- A status change is a new commit, not an in-place edit. Append a dated
  `## Status update` section instead of rewriting `Status:` silently.
- Cross-reference related ADRs in the `References` block.
- Decisions made *before* this directory existed are still worth recording —
  add them at the time the team revisits the topic.
