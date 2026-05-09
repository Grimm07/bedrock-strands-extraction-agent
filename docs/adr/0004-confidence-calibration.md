# ADR-0004: Weighted overall-confidence calibration

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

`ExtractionResult.overall_confidence` was a simple arithmetic mean of every
field's `confidence`. Two failure modes that mean masked:

1. **A missing required field had the same impact as a missing optional
   field.** A 502 was the only signal that a required field had been
   dropped; the overall confidence drifted slowly downward instead of
   collapsing to 0.
2. **Validator-passed and citation-verified fields counted no more than
   raw model self-assessments.** A field whose value matches the schema
   regex *and* whose `source_excerpt` appears verbatim in the document is
   evidently more trustworthy than a field where the model self-rated it
   `0.95` but neither check ran. The simple average obliterated that.

We use `overall_confidence` for downstream auto-approve cutoffs (the
recommended threshold is `≥ 0.85`). Without calibration, the threshold is
effectively a roulette spin.

## Decision

Replace the simple-average computation in `ExtractionService.extract` with
the weighted formula in `extraction/confidence.py`:

| Bucket                          | Weight                               |
| ------------------------------- | ------------------------------------ |
| Required field                  | × 2.0                                |
| Optional field                  | × 1.0                                |
| Validator-passed (per-field)    | × 1.2 multiplicative bonus           |
| Citation-verified (per-field)   | × 1.1 multiplicative bonus           |
| **Any** required field missing  | overall = 0.0 (hard failure)         |

Two functions:

- `compute_field_confidence(model_self, *, required, validator_passed, citation_verified)`
  applies the per-field bonuses and clamps to `[0, 1]`. The divisor `2.0`
  keeps the all-bonuses-true case ≤ 1.0.
- `compute_overall(fields, schema)` returns the required/optional-weighted
  mean of the resulting per-field confidences. Missing required collapses
  to 0.

Both are pure functions; the wire-in to `service.py` happens in Phase C2
when the multi-shot retry surfaces validator/citation outcomes per field.

## Consequences

**Positive**

- The threshold `overall_confidence ≥ 0.85` becomes meaningful: it now
  *requires* required fields to be present, *prefers* validator-clean
  values, and *rewards* citation-verified excerpts.
- A poisoned extraction (one required field missing) is auditable as
  `overall_confidence = 0.0` rather than as a soft drift.
- Per-field signal is preserved: callers that drill into `fields[].confidence`
  get the bonus-adjusted value, not the raw model self-assessment.

**Negative**

- The dashboard's average-confidence panel will drop after rollout — most
  fields are *not* citation-verified today, so removing the implicit
  upward bias of optional fields reduces the headline number even though
  the calibrated value is more honest.
- Tuning the weights is judgment-driven; we accept the values above and
  re-evaluate after Phase D5 (eval harness) provides a labelled-data
  baseline against which to measure ECE (Expected Calibration Error).
- We deliberately do not include "model_self ≥ 0.9" as a bonus — that
  feedback loop would amplify model overconfidence rather than correct it.

## References

- `src/bedrock_strands_agent/extraction/confidence.py`
- `tests/test_confidence.py`
- `src/bedrock_strands_agent/extraction/service.py:114` (the line being replaced
  in Phase C2)
- ADR-0002 — schema-first extraction (the upstream contract this ADR refines).
