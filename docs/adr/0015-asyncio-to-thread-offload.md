# ADR-0015: `asyncio.to_thread` offload at the route boundary instead of native async

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

`POST /extract` and `POST /extract/document` are FastAPI `async def`
handlers, but the underlying `ExtractionService.extract` is sync —
the Strands `Agent.__call__` it wraps is sync, the multimodal Converse
call in `extraction.multimodal.invoke_multimodal` is sync, and the
prompt rendering / parsing / validation work is sync. Calling
`service.extract(...)` directly from an `async def` route blocks the
event loop for the full Bedrock round-trip (5–30 seconds with retries
and grounding). With more than a handful of concurrent requests, the
loop saturates and queueing falls behind.

Two well-trodden options to fix this:

1. **`Agent.invoke_async` + native async all the way down.** Strands
   exposes `Agent.invoke_async`. The text path could be rewritten
   async end-to-end: `await agent.invoke_async(prompt)`, async
   `_run_extraction_loop`, async `_coerce_fields`, etc. Strands's
   `BedrockModel` already streams via `aiobotocore` under the hood
   on the streaming path.
2. **Threadpool offload at the route boundary.** Keep
   `ExtractionService.extract` synchronous; wrap each call site in
   `await asyncio.to_thread(...)`. The handler returns control to
   the event loop while the worker thread runs the blocking work.

## Decision

Threadpool offload at the route boundary, in `api/routes.py`:

```python
return await asyncio.to_thread(
    service.extract,
    document_text=body.document_text,
    schema_name=body.schema_name,
    schema_version=body.schema_version,
    document_id=body.document_id,
    correlation_id=correlation_id,
)
```

Same pattern on `/extract/document` and on the A2A executor
(`a2a/executor.py`).

`/extract/stream` keeps native async — `agent.stream_async` is async
already, and the stream-and-yield pattern only makes sense in an
async generator. So the decision is "sync paths offload to threads;
streaming stays native async."

**Alternatives considered (rejected, with reasoning):**

- **Full native async port (`Agent.invoke_async` everywhere).**
  Rejected for v0.3:
  - Touches every file in `extraction/` (`_coerce_fields`, the retry
    loop, `_invoke`, `_extract_via_vision`, `_verify_vision_grounding`)
    and every test that calls `service.extract` directly.
  - Strands' `Agent.invoke_async` returns the same `AgentResult` as
    the sync `__call__`; the async port is a mechanical rewrite,
    but rewriting 200 LOC of well-tested code to flip its async
    colour is a meaningful regression-risk surface for zero
    behavioural improvement.
  - The vision path (`invoke_multimodal`) and the prompt-rendering
    layer (Jinja `StrictUndefined` template render) have no async
    benefit — they're CPU-bound, not I/O. Forcing them into async
    accomplishes nothing besides "consistent colour."
  - The threadpool pattern has the same observable behaviour
    (event loop unblocked) at one line of code per call site.

- **Run the sync handler synchronously (drop `async def`).**
  Rejected: Starlette will run sync handlers in a threadpool
  automatically, BUT the handlers also `await` `request.body()` for
  JSON parsing and `await file.read()` for multipart uploads. We'd
  have to drop those too, which means losing FastAPI's automatic
  Pydantic body validation and multipart handling. Not worth it.

## Consequences

**Positive**

- One line of code per route to fix the event-loop blocking. Zero
  changes to `ExtractionService` and no test changes.
- Contained blast radius. The threadpool boundary is one well-known
  Python pattern; future contributors don't have to learn an async
  rewrite of the extraction code to ship a fix.
- Streaming still gets native async (`stream_async` returns an
  async iterator) — the right tool where it actually matters.

**Negative**

- The default `asyncio` thread executor caps at
  `min(32, os.cpu_count() + 4)` workers. With the documented Bedrock
  latency of 5–30 s, that's a hard ceiling of ~32 concurrent
  in-flight extractions per pod. Production deployments with higher
  concurrency must either size up the executor explicitly or run
  more pods. This is fine for the current SLO targets but is a
  ceiling worth knowing about.
- A future migration to native async (e.g., to support
  long-running streamed conversations) would still have to do the
  rewrite. The threadpool pattern doesn't pay it forward.
- The `asyncio.to_thread` boundary doesn't propagate `contextvars`
  perfectly across all logging/OTel libraries. The `correlation_id`
  ContextVar specifically does propagate (Python 3.10+ behaviour),
  and OTel's `BatchSpanProcessor` handles cross-thread spans
  correctly, but operators inheriting custom contextvars need to
  test.

## References

- [ADR-0001](0001-strands-sdk-over-langchain.md) — the Strands
  Agent's sync `__call__` is the upstream constraint.
- [ADR-0014](0014-streaming-retry-bypass.md) — explains why the
  streaming path is the exception (native async there, threadpool
  elsewhere).
- `src/bedrock_strands_agent/api/routes.py` — the two `to_thread`
  call sites on `/extract` and `/extract/document`.
- `src/bedrock_strands_agent/a2a/executor.py` — the same pattern
  inside the A2A executor.
- Python docs: `asyncio.to_thread` (added in 3.9).
