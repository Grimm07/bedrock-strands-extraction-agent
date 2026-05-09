# ADR-0010: Test-coverage gate set at 85%

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The repository enforces a CI coverage gate via pytest-cov, configured in
`pyproject.toml` under `[tool.coverage.report]` as `fail_under = 85`. A
coverage gate is non-negotiable for an extraction service that handles
customer documents: regressions in the parsing, validation, or citation
paths must be caught pre-merge, not in production. The open question is not
*whether* to gate, but at *what level* to set the floor.

The forces in play:

- The service is a typed gateway — its parsing and validation paths are
  load-bearing for every downstream consumer, so silent regressions are
  expensive.
- Two modules — `src/bedrock_strands_agent/agent/builder.py` and
  `src/bedrock_strands_agent/agent/mcp.py` — sit at the boundary where the
  service hands off to AWS Bedrock and to a child stdio subprocess
  respectively. Unit-testing past those boundaries means mocking the system
  call rather than exercising real behaviour.
- Actual coverage on `main` at the time of writing is approximately 93%,
  giving an 8-point head-room buffer above any gate set at 85%.

## Decision

CI hard-fails when total line coverage drops below 85%. The gate sits 8
points below the current measured coverage, which absorbs occasional
refactors without forcing test-padding to chase the gate.

**Alternatives considered**

- **No gate.** Rejected. Regressions would ship silently in a service
  that processes structured customer data; coverage drops would only
  surface during incident reviews.
- **Loose gate (>=70%).** Rejected. The head-room between actual coverage
  and a 70% floor is so wide that real coverage drops slip through
  unnoticed. The gate becomes ceremony rather than a guard rail.
- **Strict gate (>=90%).** Rejected. The remaining uncovered lines are
  concentrated in `agent/builder.py` and `agent/mcp.py` — the
  real-Bedrock and real-stdio-subprocess paths. Pushing the floor to 90%
  would force either flaky integration tests in CI or contrived unit
  tests that mock the system call boundary so heavily they verify
  nothing.

## Consequences

**Positive**

- The 8-point buffer tolerates honest refactors. A contributor splitting
  a 200-line module into two does not have to ship a parallel test
  rewrite in the same PR to keep CI green.
- Coverage is reported in the test summary on every PR. Operators who
  care about specific files can read the per-file table; the gate is the
  floor, not the ceiling.
- The gate composes with the existing test suite: parsing, validation,
  citation, and template paths sit comfortably above 90%, so the floor
  meaningfully protects the load-bearing surface.

**Negative**

- The coverage gaps in `agent/builder.py` and `agent/mcp.py` are
  accepted as permanent debt. They mark the boundary where the service
  hands off to AWS or to a child process, and unit-testing past that
  boundary tests the mock, not the code.
- Live integration coverage of those boundary paths is captured outside
  the unit-test gate: `tests/test_bedrock_integration.py` exercises live
  Bedrock and is opt-in via `RUN_INTEGRATION_TESTS=1`, while
  `tests/test_bedrock_boto3_e2e.py` exercises the boto3-layer wiring
  offline. Together they bound the wiring contract without contributing
  a coverage line item.
- Any future tightening to 90% should arrive bundled with either (a) a
  richer integration test rig that runs the boundary paths in CI, or (b)
  a deliberate refactor that lifts the AWS boundary higher in the call
  stack so unit tests can honestly cover more of `builder.py`. Raising
  the floor without one of those is test-padding, and is rejected on the
  same grounds as the strict-gate alternative above.

## References

- `pyproject.toml` — the `[tool.coverage.report] fail_under = 85` line
  that enforces the gate.
- `tests/test_bedrock_integration.py` — live-AWS integration coverage,
  opt-in via `RUN_INTEGRATION_TESTS=1`.
- `tests/test_bedrock_boto3_e2e.py` — offline boto3-layer wiring test
  that bounds the AWS hand-off contract.
- `docs/ROADMAP.md` — the original gate-raising decision, recorded under
  "Released since 0.2.0".
