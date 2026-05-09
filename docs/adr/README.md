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
| [0008](./0008-extraction-modes-text-vs-vision.md)                             | Two extraction paths — text vs Bedrock vision (no separate OCR) | Accepted |

## Conventions

- Filename: `NNNN-kebab-case-title.md`. Numbers are monotonic; never reuse.
- A status change is a new commit, not an in-place edit. Append a dated
  `## Status update` section instead of rewriting `Status:` silently.
- Cross-reference related ADRs in the `References` block.
- Decisions made *before* this directory existed are still worth recording —
  add them at the time the team revisits the topic.
