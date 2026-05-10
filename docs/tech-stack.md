# Technology stack

A guided tour of every runtime, library, and tool in the project — *what* it
is, *why* it is here, and *what would change* if we removed it. Use this as
the entry point when onboarding a new contributor or evaluating a swap.

For deeper rationale on the headline architectural choices, see the ADRs in
[`docs/adr/`](adr/README.md). This document cross-links to them rather than
duplicating their content.

---

## 1. Language and runtime

| Tech | Why we picked it |
| --- | --- |
| **Python 3.12+** (`requires-python = ">=3.12"`) | PEP 695 generic syntax, `typing.Self`, faster interpreter, structural-pattern-match maturity. The Strands SDK and `boto3` are first-party Python; an extraction agent's bottleneck is the model call, so language perf is not on the critical path. |
| **uv** (lockfile-managed) | A single tool replaces `pip` + `pip-tools` + `virtualenv` + `pyenv`. Resolves and locks deterministically, ships its own Python toolchain, and is fast enough that `uv sync --frozen` is viable in CI on every job. We pin to a `uv.lock` and reject unreconciled PRs (`uv sync --frozen` in CI). |
| **Hatchling** (build backend) | PEP 517 / PEP 621 native, no `setup.py`. Wheel + sdist are produced from the same `pyproject.toml`. Selected over `setuptools` for a smaller, more declarative surface. |

---

## 2. Agent runtime and model

| Tech | Why we picked it |
| --- | --- |
| **`strands-agents` + `strands-agents-tools`** | Native Bedrock support, first-class MCP, sync call semantics that match our FastAPI surface, and a small dependency surface. See [ADR-0001](adr/0001-strands-sdk-over-langchain.md). Tools are plain Python functions decorated with `@tool` — no chain DSL. |
| **Amazon Bedrock** (Claude Sonnet 4.6 default) | The customer is on AWS; Bedrock keeps inference inside their VPC, supports IAM-based access, and bills via existing AWS contracts. Sonnet 4.6 hits the accuracy/cost knee for structured extraction. The model id is overridable per-deployment via `BEDROCK_MODEL_ID`. |
| **`boto3` / `botocore`** | The official AWS SDK is the path of least resistance to Bedrock and the only path that picks up IAM, STS, and SSO credential chains transparently. Used directly in `extraction/multimodal.py` for the Converse vision API and indirectly by Strands' `BedrockModel`. |
| **`boto3-stubs[bedrock-runtime, bedrock-agent-runtime]`** (dev) | `boto3` is dynamically typed; the stubs give `mypy --strict` actual coverage of the Bedrock client surface. |
| **`tenacity`** | Bedrock's transient-error envelope (`ThrottlingException`, `ServiceQuotaExceededException`, 5xx) is exactly what `tenacity.Retrying` with `wait_exponential_jitter` was designed for. We wrap every model call in `agent/bedrock_retry.py:invoke_with_retry` and a Semgrep rule (`bedrock-call-must-be-retry-wrapped`) prevents bypass. |
| **`mcp`** (Model Context Protocol) | Lets the agent consume third-party tool servers (filesystem, fetch, …) declaratively via `mcp.config.json` without writing in-tree adapters. Strands speaks MCP natively. |

---

## 3. HTTP layer

| Tech | Why we picked it |
| --- | --- |
| **FastAPI** | Pydantic-native request/response models, auto-generated OpenAPI, `lifespan` hook for clean startup/shutdown of MCP subprocesses, ASGI middleware that composes with OTel and slowapi. Defining `ExtractionRequest`/`ExtractionResult` once and getting validation + docs from it is the productivity multiplier. |
| **Uvicorn (`uvicorn[standard]`)** | Production-grade ASGI server with `httptools` and `uvloop` extras. Used both as the dev-loop server (`make dev --reload`) and the production entry point inside the container. |
| **`pydantic` v2** | Single source of truth for the JSON contract: HTTP schemas, settings, `FormSchema`/`FieldDefinition`, and Strands' structured-output models all share Pydantic. v2's Rust-backed core matters because `/extract` validates the model's response on every call. |
| **`pydantic-settings`** | 12-factor config: every setting is a Pydantic field with type coercion and validation, automatically populated from `.env` / env vars. Removes hand-rolled `os.getenv` parsing. |
| **`python-multipart`** | Required by FastAPI for `multipart/form-data` parsing — the `POST /extract/document` upload path needs it. |
| **`slowapi`** | Per-API-key (with per-IP fallback) rate limiting on `/extract`. Decorator-based, integrates with Starlette's middleware stack, and leaves `/health` / `/metrics` / `/schemas` unbounded so probes and scrapers are not throttled. See `api/ratelimit.py`. |
| **`pyjwt[crypto]`** | Cognito JWT validation in `api/auth.py`. The `[crypto]` extra pulls in `cryptography` for RS256 signature verification; `PyJWKClient` handles JWKS fetching and `kid` rotation with a 6-hour cache. |

---

## 4. Prompt rendering

| Tech | Why we picked it |
| --- | --- |
| **Jinja2** with `StrictUndefined` | All prompts (`system.j2`, `extract.j2`, `extract_image.j2`, `retry.j2`) are templates. `StrictUndefined` makes a missing variable fail loudly at render time instead of producing a malformed prompt that silently degrades extraction quality. A Semgrep rule (`no-fstring-in-jinja-render`) blocks f-string prompt construction so contributors do not bypass the templates. |
| **`types-jinja2`** (dev) | Stubs for `mypy --strict`. |

---

## 5. Document I/O

| Tech | Why we picked it |
| --- | --- |
| **`pypdf`** | Pure-Python PDF text extraction, no native deps. Used in `extraction/document.py` to detect whether an uploaded PDF has embedded text (text path) or is image-only (rejected with guidance to rasterise client-side). v0.2 deliberately skips a rasteriser; v0.3 may add `pypdfium2` if demand justifies the dep weight. See [ADR-0008](adr/0008-extraction-modes-text-vs-vision.md). |
| **`Pillow` (PIL)** | Defence-in-depth image validation. Before shipping bytes to Bedrock vision we call `Image.verify()` so a malformed file masquerading as PNG fails locally with a clear error instead of as a Bedrock 400. |

---

## 6. Observability

| Tech | Why we picked it |
| --- | --- |
| **OpenTelemetry SDK + API** (`opentelemetry-api`, `opentelemetry-sdk`) | Vendor-neutral tracing. Resource attributes (`service.name`, `service.version`, `deployment.environment`) flow through to whatever backend the operator wires up — CloudWatch + X-Ray in production, Jaeger locally. |
| **`opentelemetry-exporter-otlp-proto-grpc`** | The OTLP/gRPC exporter is the recommended path; ships spans to a collector (Jaeger, CloudWatch, Honeycomb, …) over a single endpoint. Toggled via `OTEL_EXPORTER_OTLP_ENDPOINT`. |
| **`opentelemetry-instrumentation-fastapi`** | Automatic spans for every HTTP request with method, route, status, and headers — no manual instrumentation in routes. |
| **`opentelemetry-instrumentation-botocore`** | Automatic spans on every AWS SDK call. We always enable this even when no exporter is configured, so the trace API stays meaningfully callable for `add_event(...)` (e.g. `bedrock.retry`). |
| **`prometheus-client`** + **`prometheus-fastapi-instrumentator`** | `/metrics` endpoint with default request counters, histograms, in-flight gauges, plus any custom metrics we register. Pull-based; the operator wires Prometheus or a compatible scraper. |
| **`python-json-logger`** | Structured JSON log records with a correlation-id `ContextVar` injected into every line. Lets the log aggregator (CloudWatch, ELK, Loki) treat fields as first-class instead of regex-parsing a text format. See `logging.py`. |

Locally these are wired through the `docker/docker-compose.yml` stack:
agent → otel-collector (`otel/opentelemetry-collector-contrib:0.110.0`) →
Jaeger (`jaegertracing/all-in-one:1.62`) for span inspection at
`http://localhost:16686`.

---

## 7. Security and secret hygiene

| Tech | Why we picked it |
| --- | --- |
| **In-tree PII redaction** (`security/redaction.py`) | Spans, logs, and eval artifacts must never leak SSN/EIN/email/phone/CC/API-key shapes. The redactor runs on telemetry only — *never* on `document_text` before model invocation. A Semgrep rule (`no-print-of-document-text`) prevents accidental prints. |
| **`bandit`** (dev / pre-commit / CI) | Static security linter for common Python footguns (subprocess shell=True, hardcoded passwords, weak crypto). Configured via `[tool.bandit]` in `pyproject.toml`. |
| **`pip-audit`** (dev / `make audit`) | Vulnerability scanning of the synced venv against the PyPI advisory db. Runs on the live venv (not `requirements.txt`) so the editable project does not trip the "not on PyPI" branch. |
| **`semgrep`** (CI workflow `semgrep.yml`) | Project-specific rules in `.semgrep.yml`: enforce the redaction discipline, prompt-rendering discipline, and Bedrock-retry-wrapping discipline at every PR. |
| **`gitleaks`** (pre-commit + CI workflow) | Catches AKIA/ASIA/sk_/pk_-shaped strings before they hit `origin`. `.gitleaks.toml` allowlists test fixtures and Bedrock model-id shapes that look like API keys. |
| **CodeQL** (workflow `codeql.yml`) | GitHub's semantic SAST. Catches a different class of issues than Bandit/Semgrep and is free for the repo. |
| **Trivy** (used in container CI) | Image-layer vulnerability scanning so a compromised base image is caught at build time. |

---

## 8. Quality gate (`make check` / `make ci`)

| Tech | Why we picked it |
| --- | --- |
| **`ruff`** (lint + format) | One Rust-backed tool replaces `flake8`, `isort`, `pyupgrade`, `black`, `pep8-naming`, and the docstring linters. Configured in `pyproject.toml` with a strict ruleset (`E, W, F, I, B, C4, UP, SIM, RUF, S, D, ANN, ASYNC, PT, RET, TRY, PERF`). Fast enough to run on every save and on every pre-commit hook. |
| **`mypy --strict`** | Type-checks the entire `src/` tree with `strict = true`, `disallow_any_generics`, and the Pydantic plugin. The Bedrock contract is JSON-shape-critical and silent type drift is the failure mode we are most paranoid about. |
| **`pytest`** + `pytest-asyncio` + `pytest-cov` + `pytest-mock` | Standard test stack. `asyncio_mode = "auto"` because routes are async; `--strict-markers` and `--strict-config` make typos in markers/config fail loudly. Coverage threshold is `fail_under = 85` (currently ~91%) and warnings are promoted to errors (`filterwarnings = ["error"]`) so deprecations cannot rot in silently. |
| **`httpx`** + **`respx`** (dev) | `httpx` is the test client the FastAPI `TestClient` is built on; `respx` mocks outbound HTTP for tests that exercise network paths without hitting them. |
| **`pre-commit`** | Locks the toolchain version of ruff/mypy/bandit/gitleaks per repo, so a contributor's environment cannot quietly drift. `make hooks` runs the same hooks CI runs. |

---

## 9. CI / CD and supply chain

| Tech | Why we picked it |
| --- | --- |
| **GitHub Actions** | The repo lives on GitHub; first-party CI keeps the toolchain footprint small. Workflows split by concern: `ci.yml` (test + lint + typecheck), `codeql.yml`, `gitleaks.yml`, `semgrep.yml`, `docs.yml` (mkdocs publish), `release.yml`. |
| **Renovate** (`renovate.json5`) | Automated dependency updates grouped by ecosystem (`strands-*`, `opentelemetry-*`, `boto3*`, dockerfile). Patch-level dev-dep bumps auto-merge; runtime deps require human review. We use Renovate over Dependabot because Renovate can drive `uv lock` via `postUpdateOptions: ["uvLockFile"]` — Dependabot cannot reconcile `uv.lock` and our CI rejects unreconciled PRs. |
| **Docker (multi-stage)** | The `Dockerfile` uses `ghcr.io/astral-sh/uv` as the builder for cache-friendly `uv sync` and `python:3.12-slim-bookworm` as the runtime. Non-root user (`uid 10001`), bytecode-compiled venv, `HEALTHCHECK` against `/health`. |
| **devcontainer** (`.devcontainer/`) | Reproducible local dev for VS Code / GitHub Codespaces. Same Python, same uv, same hooks as CI. |

---

## 10. Documentation

| Tech | Why we picked it |
| --- | --- |
| **MkDocs Material** (`mkdocs-material`) | Markdown-first docs site with search, navigation tabs, and admonitions. Cheap to run on GitHub Pages. |
| **mkdocstrings (`mkdocstrings[python]`)** | Auto-generates the API reference (`docs/api.md`) from Google-style docstrings, so the rendered API never drifts from the code. |
| **`mkdocs-gen-files`** | Programmatically composes navigation entries (used by the ADR index). |
| **ADRs** (`docs/adr/`) | Lightweight architecture decision records — every load-bearing choice gets a one-page ADR with Context / Decision / Consequences. Linked from this doc and from `docs/index.md`. |

---

## 11. What we deliberately did *not* pick

| Considered | Rejected because |
| --- | --- |
| **LangChain / LangGraph** | Deeper transitive deps, chain DSL hides where Bedrock errors land, less ergonomic with strict Pydantic. See [ADR-0001](adr/0001-strands-sdk-over-langchain.md). |
| **LlamaIndex** | RAG-first; orthogonal to form extraction. See [ADR-0001](adr/0001-strands-sdk-over-langchain.md). |
| **`requests`** | Sync-only and not what the AWS SDK uses; we already have `httpx` for tests and `boto3` for AWS. Adding a third HTTP client is gratuitous. |
| **`black` + `isort` + `flake8`** | Subsumed by `ruff`. One config, one binary, ~10× faster. |
| **`setuptools`** | `hatchling` is PEP-621-native with a smaller config and equivalent wheel output. |
| **`pip` + `pip-tools`** | `uv` does both, faster, with a lockfile we actually trust. |
| **Dependabot for Python** | Cannot reconcile `uv.lock`; Renovate can. |
| **A PDF rasteriser (`pypdfium2`, `pdf2image`)** | Native deps and a heavy install footprint for a v0.2 feature with unproven demand. Image-only PDFs are rejected with a clear "rasterise client-side" message. Revisit in v0.3. See [ADR-0008](adr/0008-extraction-modes-text-vs-vision.md). |
| **Sync-style HTTP client in tests** | `httpx` covers both sync and async with one API; FastAPI's `TestClient` is built on it. |

---

## 12. Quick lookup: file → tech

| File | Touches |
| --- | --- |
| `src/bedrock_strands_agent/api/app.py` | FastAPI, Uvicorn, slowapi, prometheus-fastapi-instrumentator, OTel-FastAPI |
| `src/bedrock_strands_agent/api/auth.py` | PyJWT, Starlette middleware |
| `src/bedrock_strands_agent/api/ratelimit.py` | slowapi |
| `src/bedrock_strands_agent/agent/builder.py` | Strands `Agent` + `BedrockModel`, MCP, Jinja2 |
| `src/bedrock_strands_agent/agent/bedrock_retry.py` | tenacity, OTel `add_event` |
| `src/bedrock_strands_agent/agent/mcp.py` | `mcp` SDK |
| `src/bedrock_strands_agent/agent/prompts.py` | Jinja2 (StrictUndefined) |
| `src/bedrock_strands_agent/agent/tools.py` | Strands `@tool` |
| `src/bedrock_strands_agent/extraction/service.py` | Pydantic v2, Strands `Agent`, OTel |
| `src/bedrock_strands_agent/extraction/document.py` | pypdf, Pillow |
| `src/bedrock_strands_agent/extraction/multimodal.py` | boto3 (`bedrock-runtime` Converse) |
| `src/bedrock_strands_agent/security/redaction.py` | stdlib `re` (no deps) |
| `src/bedrock_strands_agent/telemetry.py` | OTel SDK + OTLP gRPC + Botocore instrumentor |
| `src/bedrock_strands_agent/logging.py` | python-json-logger, `ContextVar` |
| `src/bedrock_strands_agent/config.py` | pydantic-settings |
| `Dockerfile` | uv builder, python:3.12-slim runtime |
| `docker/docker-compose.yml` | otel-collector-contrib, jaeger all-in-one |
| `pyproject.toml` | hatchling, ruff, mypy, pytest, bandit, coverage |
| `.pre-commit-config.yaml` | pre-commit, ruff, mypy, bandit, gitleaks |
| `.semgrep.yml` | semgrep (project rules) |
| `renovate.json5` | Renovate |
