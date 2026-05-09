# ADR-0009: Cap the self-correcting retry loop at two re-prompts (three total attempts)

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

ADR-0002 established the schema-first extraction contract and introduced a
self-correcting retry: when the model's response fails schema validation
or citation verification, we re-prompt it with the validator and citation
errors embedded in the prompt (see
`src/bedrock_strands_agent/templates/retry.j2`) so it can fix its own
output. ADR-0002 fixed the *shape* of that loop but deliberately deferred
the *bound* — how many times we are willing to re-prompt before giving up.

The unbounded version of that loop trades latency and cost for completion
rate. Every additional retry is another full Bedrock round-trip carrying
the document, the schema, and the prior errors; on a pathological input
the loop can burn five-plus model calls before either succeeding or
hitting an arbitrary timeout. Because each underlying call is itself
retried by the tenacity-backed transient-error policy in
`src/bedrock_strands_agent/agent/bedrock_retry.py`, the worst-case fan-out
compounds multiplicatively.

We need a single, defensible cap that:

1. Recovers the easy-win case where the second (or third) attempt, armed
   with the validator's diagnostics, produces a valid result.
2. Bounds tail latency and per-request cost tightly enough to support a
   service-level SLO and predictable customer billing.
3. Surfaces "this document is genuinely unextractable" as a real, visible
   failure rather than burying it under retry-loop noise.

## Decision

`ExtractionService` is constructed with `max_retries=2` as the hard-coded
default (`src/bedrock_strands_agent/extraction/service.py:92`); the retry
loop performs at most two self-correction attempts on top of the initial
call, for a maximum of **three total Bedrock invocations** per request.
If the third attempt also fails validation, the service returns a
structured `ErrorResponse` (502 for upstream/model failure, 422 for
unrecoverable schema violation) listing the validator errors verbatim.
The constructor parameter remains tunable so a future workload can raise
it deliberately, but the default is the contract.

**Alternatives considered:**

- **Zero retries (fail-fast).** Rejected. Schema-validation failures are
  common on edge-case forms — empty optional fields rendered as `null`
  versus omitted, ambiguous date formats, unit suffixes inside numeric
  cells — and the second attempt with the validator's explicit error
  feedback usually succeeds. Throwing away that recovery loses real
  accuracy at trivial latency cost (one extra call on the failure path
  only).
- **One retry only (`max_retries=1`).** Rejected. Empirical observation
  during v0.1/v0.2 evaluation showed a non-trivial slice of documents
  recover on the *third* attempt — typically when the first re-prompt
  fixes one validator error but unmasks another. A second re-prompt
  carrying the full error history closes that gap. The latency/cost
  delta over `max_retries=1` is a single extra call only on the failure
  path.
- **Unlimited retries with backoff.** Rejected. Tail latency becomes
  unbounded; one session can burn five-plus Bedrock calls on a pathological
  document; p95 latency and cost both blow up; and the observability
  signal of "the document is genuinely unextractable" disappears under
  retry-loop noise. Capacity planning becomes a guess.
- **Caller-configurable per-request.** Rejected. The cost-and-latency
  bound is a service-level SLO, not a per-call concern. Allowing callers
  to pass arbitrary `max_retries` values negates the SLO and shifts the
  cost ceiling onto whichever caller is least disciplined. The
  constructor parameter is the right knob: it sits with the operator who
  owns the SLO, not the API consumer.

## Consequences

**Positive**

- **Bounded worst-case latency.** p95 is capped at `3 ×` single-call
  latency for the self-correction loop. Combined with the tenacity-backed
  transient-error retry on each call (`agent/bedrock_retry.py`), the
  absolute worst-case Bedrock-call count per request is
  `3 × (1 + max_transient_retries)` — predictable and dashboardable.
- **Bounded worst-case cost.** Per-request token spend is at most
  `3 ×` per-call tokens. Capacity planning, rate-limit budgeting, and
  customer billing all benefit from the hard ceiling.
- **Preserved observability.** When the third attempt also fails, the
  caller sees a structured `ErrorResponse` containing the validator
  errors. They can decide to retry the request, lower the schema
  strictness, or escalate to human review. SLO dashboards see genuine
  unextractable-document failures clearly, rather than as soft drift in
  the retry-storm tail.
- **Recovers the easy *and* moderately-hard wins.** The two-retry cap
  captures the bulk of the recoverable accuracy that an unbounded loop
  would — empirically, the second attempt with validator feedback
  succeeds at a much higher rate than the first; the third attempt
  catches the residual cases where one error masks another. Subsequent
  attempts have steeply diminishing returns.

**Negative**

- **A small tail of recoverable cases is lost.** Documents that would
  have succeeded on the fourth or fifth attempt now surface as failures.
  We accept this; the cost of keeping the loop open for those cases is
  paid by every request, not just the ones that benefit.
- **The cap is a judgement call, not a measurement.** We have not yet
  produced a labelled-data study comparing per-attempt success rates on
  production traffic. The eval harness deferred to Phase D5 should
  produce that data; if it demonstrably justifies `max_retries=3` (or
  reducing to `1`) for a specific workload, the constructor accepts it
  and the SLO would need to be re-priced.
- **Operators must resist drift.** The constructor parameter is
  attractive to tune upward whenever an extraction failure lands in a
  support ticket. Raising it is a real SLO change, not a quick fix; that
  needs to be enforced by review rather than by code.

## References

- ADR-0001 — Strands runtime over LangChain (the framework whose Agent
  contract this loop sits on top of).
- ADR-0002 — Schema-first extraction with the self-correcting retry
  (this ADR fixes the cap that ADR-0002 deferred).
- `src/bedrock_strands_agent/extraction/service.py:92` — the
  `max_retries=2` constructor default and the retry loop body.
- `src/bedrock_strands_agent/agent/bedrock_retry.py` — the tenacity-backed
  transient-error retry that wraps each Bedrock call (the inner loop
  this ADR's outer cap multiplies against).
- `src/bedrock_strands_agent/templates/retry.j2` — the retry-prompt
  template that embeds validator and citation errors for re-attempts.
