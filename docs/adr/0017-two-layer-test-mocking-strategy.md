# ADR-0017: Two-layer test mocking — Strands Agent for behaviour, boto3 for wiring

- **Status:** Accepted
- **Date:** 2026-05-09
- **Authors:** bedrock-strands-agent contributors

## Context

The service has two layers between the test and Bedrock that can be
mocked, and the choice of which to mock has real consequences:

```
ExtractionService.extract
  ├── PromptRenderer.extract      (Jinja2, no I/O)
  ├── _bundle.agent(prompt)       ← Strands Agent layer (mockable)
  │     └── BedrockModel.stream(...)
  │           └── boto3 bedrock-runtime client.converse_stream(...)  ← boto3 layer (mockable)
  └── _coerce_fields(payload, ...)
```

If we mock too high (the `Agent`), tests are fast and easy to write,
but they pass even when the boto3 wiring is broken — wrong `modelId`,
wrong region, wrong request shape, wrong `Converse` vs
`ConverseStream` choice. If we mock too low (the boto3 client), tests
become brittle to Strands SDK changes (event shapes, streaming
events, retry semantics).

Tests that ran the whole stack against real Bedrock would catch
both, but cost real money per CI run, require AWS credentials in CI,
and are flaky on throttling. Not viable as the default path.

## Decision

**Two layers of mock, used in different test files for different
purposes:**

### Layer A — Strands Agent mock (the default for behaviour tests)

`tests/conftest.py::stub_extraction_service` builds a real
`AgentBundle` whose `agent` is a `MagicMock` returning canned JSON.
This is the fixture used by:

- `tests/test_api.py` — every HTTP route test.
- `tests/test_extraction_service.py` — every unit test of the
  extraction pipeline.
- `tests/test_a2a.py` — A2A protocol tests.
- `tests/test_document.py` — multipart upload tests (with a separate
  `_image_only_pdf_bytes()` helper for PIL-generated scanned-PDF
  fixtures).

What it covers: the schema-first contract, validators, citations,
retry loop, vision grounding, A2A wiring, FastAPI middleware. What
it deliberately does NOT cover: whether the Strands `BedrockModel`
sends the right `modelId` to the right Bedrock API.

### Layer B — boto3 `BaseClient._make_api_call` patch (one targeted test)

`tests/test_bedrock_boto3_e2e.py` patches `BaseClient._make_api_call`
via `monkeypatch` to intercept the `bedrock-runtime` `ConverseStream`
call. The Strands Agent runs unmocked end-to-end through `BedrockModel`
and into boto3; the patch returns a stubbed event-stream that decodes
to the same canned JSON the layer-A fixture uses.

What it covers: `BedrockModel` wiring, the `modelId` flowing from
`Settings.bedrock_model_id` through to the Bedrock API call, the
streaming event-stream consumption path, the request shape Strands
builds. What it does NOT cover: the rest of the extraction pipeline
(that's already covered by layer A).

The two layers complement: any regression in
`Settings.bedrock_model_id` propagation, `BedrockModel` constructor
arguments, or Strands' choice between `Converse` and `ConverseStream`
fails layer B; any regression in `_coerce_fields`, validators, or
the retry loop fails layer A. We only need ONE layer-B test (the
wiring contract is small) but EVERY behavioural test goes through
layer A.

**Alternatives considered (rejected):**

- **`botocore.stub.Stubber`** for the boto3-layer test. Strands'
  `BedrockModel` defaults to streaming
  (`ConverseStream` rather than `Converse`); Stubber can't return
  event-streams (the `stream` field of the response is hydrated
  from a live HTTP body). `_make_api_call` patching returns a plain
  dict whose `stream` key is an iterator we control — works.
- **Mock at the `invoke_with_retry` level** to test retry behaviour
  in isolation — actually IS done; see
  `tests/test_bedrock_retry.py`. That's a third layer used only for
  the retry-helper unit tests, not as a substitute for the
  Agent-layer or boto3-layer tests.
- **Real-Bedrock integration test as the default**.
  `tests/test_bedrock_integration.py` exists for this — gated on
  `RUN_INTEGRATION_TESTS=1`, skipped by default. Useful for occasional
  manual verification; not viable as a CI gate (cost + flakiness).

## Consequences

**Positive**

- The unit-test suite is fast (~5 seconds for 246 tests), runs
  offline, requires no AWS credentials, and gives meaningful coverage
  of the schema/validation/retry pipeline.
- The boto3-layer e2e test catches the wiring regressions that the
  Strands Agent mock can't see (e.g., a typo in the `BedrockModel`
  constructor, a misnamed kwarg, a wrong cross-region inference
  profile). One test, ~180 lines.
- Test files map cleanly to layers: a developer adding a new
  validator writes a layer-A test; a developer touching
  `agent/builder.py` adds a layer-B assertion. The CLAUDE.md
  documents this convention so new contributors don't second-guess.
- The k6 load-test stub-mode CI workflow
  (`.github/workflows/load-test.yml`) uses
  `scripts/_boot_with_stub.py` — a third stand-in that boots the
  full FastAPI app with a layer-A-style mocked agent. Same logical
  layer, exposed over HTTP, used for HTTP-side regression detection.

**Negative**

- A regression that's invisible to BOTH layers (e.g., Strands
  changes the `BedrockModel.stream` event shape between releases in
  a way that affects token-by-token text extraction but not the
  final `result` event) is uncaught until either the layer-B test
  is updated to assert intermediate events OR a manual
  integration run trips on it.
- Two layers of mock means two patterns to teach. The CLAUDE.md
  carries both, but a contributor new to the project has to read
  it before they understand which one to use for which test.
- The layer-B test is brittle to Strands SDK upgrades: when
  `BedrockModel` is refactored (e.g., changed from
  `ConverseStream` to a different op), the test has to follow.
  Acceptable cost; documented in the test's docstring.

## References

- [ADR-0001](0001-strands-sdk-over-langchain.md) — Strands SDK choice
  (the upstream we mock at layer A).
- [ADR-0010](0010-coverage-gate-85.md) — the 85% coverage gate;
  layer-B is the only thing keeping `agent/builder.py` from being a
  permanent gap rather than a tested boundary.
- `tests/conftest.py::stub_extraction_service` — the layer-A
  fixture.
- `tests/test_bedrock_boto3_e2e.py` — the layer-B test.
- `tests/test_bedrock_integration.py` — the opt-in real-Bedrock
  integration test.
- `tests/test_bedrock_retry.py` — the unit tests on
  `invoke_with_retry`.
- `scripts/_boot_with_stub.py` — the layer-A-equivalent stand-in
  used by the k6 CI workflow.
- `CLAUDE.md` — captures the two-layer convention for contributors.
