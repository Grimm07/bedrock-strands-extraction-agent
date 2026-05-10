# ADR-0014: `/extract/stream` bypasses the self-correcting retry loop

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The text-mode `/extract` endpoint runs the full self-correcting retry
loop documented in [ADR-0009](0009-bounded-self-correcting-retry.md):
up to 3 total attempts, with each retry receiving the prior attempt's
validator/citation errors so the model can self-correct.
`/extract/stream` exposes the same extraction over Server-Sent Events
so callers can render text deltas as the model emits them — a UX win
when extraction takes 5–10 seconds end-to-end.

The two designs collide. If the streaming endpoint also runs the
retry loop, what does the caller experience on a validation failure?

1. **Replay the chunks** — the client sees the first attempt's
   token-by-token text, then the second attempt's, then the third.
   Confusing UX (the document seems to repeat), and the JSON the
   client may have started rendering is now stale.
2. **Buffer until success, then stream** — defeats streaming entirely
   (caller waits for end-of-attempt before any chunks arrive).
3. **Stream attempt 1, swallow it on validation failure, restart** —
   client sees torn output and a confusing restart, plus we've
   doubled the latency the streaming UI was meant to reduce.
4. **Skip the retry loop on the stream path** — single attempt,
   stream the deltas, surface the first-pass result (or error) at
   end-of-stream. Callers that want the retry loop fall back to
   `POST /extract`.

## Decision

Streaming is **single-attempt by design**. The streaming method
`ExtractionService.extract_stream`:

- Calls `agent.stream_async(prompt)` once. Yields `event: chunk`
  frames for every text delta from the model.
- Accumulates the deltas into a buffer.
- On stream completion, runs `_parse_json` + `_coerce_fields` on the
  accumulated text exactly once (no retry on validation failure).
  Yields `event: result` on success, `event: error` on parse or
  coerce failure.
- Does not run vision-mode grounding (vision is text-mode only over
  SSE — multipart upload is the vision path).
- Does not open `extraction.attempt` sub-spans (no retries to count;
  see [ADR-0013](0013-opentelemetry-observability-strategy.md)).

The streaming and non-streaming paths share `_parse_json`,
`_coerce_fields`, the validator pipeline, citation verification, and
all the prompt-injection defences from
[ADR-0011](0011-prompt-injection-threat-model.md). The contract is
"one shot, full validation, no self-correction."

**Alternatives considered (rejected):**

- **Replay-on-failure** — UX disaster. Streaming clients already
  render partial content; restarting mid-stream creates confusing
  "wait, was it INV-1234 or INV-5678?" experiences.
- **Buffer-then-stream** — defeats the purpose. The latency win of
  SSE is "first byte fast"; buffering eats it.
- **Stream then re-prompt invisibly** — the second prompt is in
  natural language ("fix these errors") and the model's output is a
  full new JSON object. The client would either see two completions
  (confusing) or only the latest (no streaming win on the retry).

## Consequences

**Positive**

- Streaming clients get genuine first-byte latency wins. The model
  starts emitting deltas in 100–300 ms; the full JSON arrives in
  3–5 s; the caller can render progress as it goes.
- The JSON-RPC + SSE state machine stays simple. There's no
  ambiguity about what `event: chunk` means or whether the next
  chunk is from the same attempt.
- Telemetry stays clean: `extraction.stream` is a single span with
  no children, vs. `extraction.run → extraction.attempt × N` for
  the non-streaming path. Easy to filter in trace search.

**Negative**

- Streaming clients lose the self-correcting retry safety net.
  Validation failures bubble out as `event: error`; the caller has
  to handle the failure mode rather than transparently waiting
  longer for a corrected response.
- The recommended fallback (call `POST /extract` after a
  stream-side error) doubles the perceived latency for unlucky
  documents — first the failed stream, then the synchronous retry
  loop. Callers that care about strict completion rate over
  latency should use `/extract` directly.
- Per-field validation is not surfaced mid-stream. Callers that
  want "show field X as soon as it's valid" need a future
  partial-field validation enhancement (still deferred).

## References

- [ADR-0009](0009-bounded-self-correcting-retry.md) — the retry loop
  this endpoint deliberately bypasses.
- [ADR-0011](0011-prompt-injection-threat-model.md) — the input-side
  defences that DO apply to streaming requests (the streaming path
  shares the same template + parser pipeline).
- [ADR-0013](0013-opentelemetry-observability-strategy.md) — span
  hierarchy implication.
- `src/bedrock_strands_agent/extraction/service.py:extract_stream`.
- `src/bedrock_strands_agent/api/routes.py` — the SSE event-formatter
  + the `extract_stream` route handler.
- `tests/test_api.py` — streaming end-to-end tests.
