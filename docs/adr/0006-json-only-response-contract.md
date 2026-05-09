# ADR-0006: JSON-only response contract

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The extraction service's wire format is a typed Pydantic model
(`ExtractionResult`) with a fixed `{"fields": [...]}` shape. The model on the
other end of every Bedrock invocation is a general-purpose LLM that, by
default, behaves like a chat assistant: it readily wraps JSON in Markdown
fences (` ```json ... ``` `), prefixes responses with prose ("Here is the
extracted data:"), appends commentary ("Let me know if you need anything
else."), or interleaves explanations between top-level keys.

Anything other than raw JSON breaks downstream parsing. `json.loads` on a
fenced block raises `JSONDecodeError`; a stray "Here you go:" prefix does
the same. Because the service is the typed gateway between the model and
every consumer (the FastAPI handlers, the eval harness, downstream agent
hops in Phase D), the response shape is non-negotiable — it has to be parsed
deterministically on every call.

The forces in play:

- We support the full Claude family on Bedrock (Haiku, Sonnet, Opus across
  versions) plus, in principle, non-Claude Bedrock models. Their support for
  vendor-specific structured-output features is uneven.
- The same parser must serve both the text path (`/extract`) and the vision
  path (`/extract/document`), so the contract has to live above the modality
  layer.
- Observability matters: we want a clear signal when the model goes
  off-script, not a parser that papers over it.

## Decision

The contract is "JSON-only" at the wire level: every successful response
deserialises into `ExtractionResult`. We enforce it as a *prompt-first,
parser-tolerant* layered defence rather than a strict reject-on-fence rule:

1. **Prompt-side instruction.** Every Jinja template under
   `src/bedrock_strands_agent/templates/*.j2` that frames a model response
   ends with an explicit line instructing the model to return raw JSON
   only, with no Markdown fences and no surrounding prose. The system
   prompt (`templates/system.j2`) declares the JSON contract; the per-
   request prompts (`extract.j2`, `extract_image.j2`) repeat the constraint
   immediately before the model speaks. Templates render with
   `StrictUndefined`, so a missing variable raises `TemplateError` at
   render time rather than silently producing a malformed prompt that the
   model would then echo garbage back at.
2. **Tolerant parser.** `_parse_json` in
   `src/bedrock_strands_agent/extraction/service.py` strips Markdown fences
   (`_FENCE_RE`) and, if `json.loads` still fails, extracts the first JSON
   object from surrounding prose via `_JSON_BLOCK_RE`. Once a JSON object
   is recovered, `_coerce_fields` and the validators in
   `extraction.validators` enforce the actual schema contract. The same
   pair backs both `/extract` and `/extract/document`, so the contract is
   uniform across modalities.

The retry loop (ADR-0002, ADR-0009) is reserved for *schema* failures —
missing required fields, type coercion errors, citation mismatches. A
fenced response on attempt 1 succeeds silently; only when the JSON inside
the fences also fails schema validation does the model get a second
chance.

**Alternatives considered**

- **Bedrock structured outputs / tool-use forced JSON.** Rejected. Support
  for vendor-specific JSON-mode features is uneven across the Claude family
  and across non-Claude Bedrock models. The prompt-only approach works on
  every Claude version and degrades cleanly to other Bedrock models with no
  code change. The cost of *not* using structured outputs is one prompt
  instruction line per request — cheaper than a per-model handshake and
  cheaper than maintaining model-capability branches in the service.
- **Strict parser — reject any fence or prose.** Rejected. Models
  occasionally emit a stray fence even when instructed not to, and burning
  a full Bedrock retry on a cosmetic deviation is wasteful (latency + cost)
  when the JSON itself is already correct. Schema-level retries catch the
  cases that genuinely matter; cosmetic ones don't.
- **Bespoke recursive-descent JSON-extracting parser.** Rejected. The
  two-stage `_FENCE_RE` strip + `_JSON_BLOCK_RE` brace match is enough for
  every observed model phrasing and is exhaustively unit-tested
  (`tests/test_extraction_service.py`). A heavier custom parser would add
  a maintenance surface without observable benefit.

## Consequences

**Positive**

- Validation is uniform across the text path (`/extract`) and the vision
  path (`/extract/document`); both terminate in the same `_parse_json` →
  `_coerce_fields` → validator chain.
- The cost of the contract is a single prompt instruction line per request
  — no extra latency, no structured-outputs handshake, no model-specific
  branches in service code.
- Cosmetic model deviations (a stray ` ```json` fence, a one-line preamble)
  do not burn a retry. Schema retries are reserved for things that
  actually matter to the consumer.
- The contract composes with ADR-0002's schema-first extraction: the model
  is told *what* shape and *that* it must be raw — both constraints arrive
  in the same prompt.

**Negative**

- Tolerating fences/prose at parse time means we lose visibility into
  cosmetic model deviations. We do not currently log "the parser had to
  strip a fence"; if regression-detection on prompt drift becomes valuable,
  add a counter at `_parse_json` for the strip path.
- The constraint relies on prompt discipline. Template authors must
  remember the trailing instruction line; the template-reviewer subagent
  (`.claude/agents/prompt-template-reviewer.md`) enforces this on every PR
  but the discipline is real.
- We forgo any vendor-side compliance guarantees that structured-output
  features would provide. The trade is portability across the Claude
  family (and non-Claude Bedrock models) for rare false-negatives that the
  schema retry absorbs.

## References

- ADR-0002 — schema-first extraction with self-correcting retry (the loop
  that absorbs schema-contract violations).
- ADR-0009 — retry policy (the cap on the loop that tolerates JSON
  deviations only when the *schema* fails).
- `src/bedrock_strands_agent/templates/system.j2`, `extract.j2`,
  `extract_image.j2`, `retry.j2` — the JSON-only instruction sites.
- `src/bedrock_strands_agent/extraction/service.py` — `_FENCE_RE`,
  `_JSON_BLOCK_RE`, `_parse_json`, and `_coerce_fields`.
- `src/bedrock_strands_agent/extraction/validators.py` — post-parse field
  validation that runs once the JSON contract is satisfied.
- `.claude/agents/prompt-template-reviewer.md` — review hook that guards
  the trailing instruction line on every template change.
