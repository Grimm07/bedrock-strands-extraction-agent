# Cost breakdown

Operating-cost estimate for `bedrock-strands-agent` at three traffic
bands, plus the cost-per-request math for each extraction path. Numbers
are AWS US-region list prices as of 2026-05; cross-region inference,
volume discounts, and committed-use plans (Savings Plans, Reserved
Capacity) are noted where relevant but not factored in.

> **Calibration disclosure.** Token counts for input/output are
> empirical estimates from rendering each prompt with the production
> templates against a representative invoice schema. Real workloads
> will differ; use the per-request math below as a planning model and
> reconcile against CloudWatch + Bedrock invocation logs after one
> billing cycle of real traffic.

---

## Per-request cost — Bedrock model invocations (the dominant line)

Default model is **Claude Sonnet 4.6** on Bedrock cross-region inference
(`us.anthropic.claude-sonnet-4-6-20250514-v1:0`). Bedrock standard-tier
pricing for Claude Sonnet 4.6 in US regions:

| Direction | Price |
|---|---|
| Input tokens | **$3.00 / 1M** |
| Output tokens | **$15.00 / 1M** |

(Cross-region inference profiles do **not** add a per-token markup; they
let the request route to whichever US region has capacity. There is no
per-image surcharge for vision — image tokens are billed as input.)

### Per-call token estimates

Each row is a single Bedrock Converse call.

| Path | Input tokens | Output tokens | Per-call cost |
|---|---|---|---|
| `/extract` text (typical 1-page document) | ~3,000 | ~1,200 | ~$0.027 |
| `/extract` text (8-page document) | ~12,000 | ~1,500 | ~$0.058 |
| `/extract/document` text (PDF with embedded text) | same as `/extract` text | | |
| `/extract/document` vision (1 page image at 200 DPI) | ~4,000 (incl. 1.5K image tokens) | ~1,200 | ~$0.030 |
| `/extract/document` vision **+ grounding** (1 page) | 2× the row above | | **~$0.060** |
| `/extract/document` vision (5-page rasterised PDF) | ~10,000 (incl. ~7.5K image tokens) | ~1,500 | ~$0.052 |
| `/extract/document` vision **+ grounding** (5 pages) | 2× the row above | | **~$0.104** |
| `/extract/stream` (text, no retry by design) | ~3,000 | ~1,200 | ~$0.027 |

### Per-request worst-case (with retry budget exhausted)

`max_retries=2` (ADR-0009) means up to 3 total attempts on
`/extract` and `/extract/document`. Streaming bypasses the retry loop
(`/extract/stream` is a single attempt).

| Path | Worst case | Cost ceiling |
|---|---|---|
| `/extract` text | 3 attempts | ~$0.081 |
| `/extract/document` text-mode PDF | 3 attempts | ~$0.081 |
| `/extract/document` vision (1 page) | 3 extract + 3 grounding = 6 calls | ~**$0.180** |
| `/extract/document` vision (5 pages) | 3 extract + 3 grounding = 6 calls | ~**$0.312** |
| `/extract/stream` | 1 attempt | ~$0.027 |

The vision path is the expensive one because grounding (ADR-0011)
doubles the per-attempt cost. The retry budget is the operator's lever
to cap worst-case spend; tightening to `max_retries=1` would halve the
text-mode ceiling and reduce vision worst-case from 6 calls to 4
(initial extract + 1 retry extract + 2 groundings = 4).

---

## Monthly cost — Bedrock at three traffic bands

Assumptions: average per-request cost is **median**, not worst case
(retries are uncommon; grounding always runs in vision but the retry
loop trips on a small fraction of requests).

### Text-only workload (`/extract`)

| Daily requests | Per-day | Per-month |
|---|---|---|
| 1,000 | $27 | **~$810** |
| 10,000 | $270 | **~$8,100** |
| 100,000 | $2,700 | **~$81,000** |

### Vision-mode workload (`/extract/document` images, with grounding)

| Daily requests | Per-day | Per-month |
|---|---|---|
| 1,000 | $60 | **~$1,800** |
| 10,000 | $600 | **~$18,000** |
| 100,000 | $6,000 | **~$180,000** |

### Mixed (80% text, 20% vision)

| Daily requests | Per-day | Per-month |
|---|---|---|
| 1,000 | $34 | **~$1,000** |
| 10,000 | $336 | **~$10,000** |
| 100,000 | $3,360 | **~$100,000** |

> Vision is materially more expensive. If a workload has a long-tail of
> image-only documents, **per-tenant routing** (hint the path on the way
> in) is the cheapest cost lever. Grounding can also be made
> conditional once the eval harness validates whether it's worth it on
> a given workload — see roadmap item Phase D5.

---

## Compute — three deployment shapes

ADR-0003 proposes **AgentCore Runtime** as the target. Until that
infrastructure exists, the realistic alternatives are **ECS Fargate**
(simpler, well-understood) and **Lambda** (cheap if traffic is bursty
and short).

### ECS Fargate (recommended for v0.3)

One service, autoscaled task pool. Pricing per vCPU-hour and GB-hour:

| Component | Price |
|---|---|
| vCPU | $0.04048 / vCPU-hour |
| Memory | $0.004445 / GB-hour |

A 1 vCPU + 2 GB task running 24×7 = `(0.04048 + 0.004445×2) × 730 ≈ $36/month`.

| Tasks | Monthly compute |
|---|---|
| 1 task (dev / staging) | ~$36 |
| 2 tasks (prod baseline, multi-AZ) | ~$72 |
| 4 tasks (prod under typical load) | ~$144 |
| 10 tasks (sustained 100k req/day burst) | ~$360 |

Add **ALB** for HTTPS termination: ~$22/month base + $0.008/LCU-hour
(~$5–25/month at modest traffic).

### Lambda

`uvicorn` doesn't run natively on Lambda; would need Mangum or
`aws-lambda-web-adapter`. Bedrock calls take 1–10 seconds, so
per-invocation Lambda time is meaningful:

| Component | Price |
|---|---|
| Request | $0.20 / 1M requests |
| Compute | $0.0000166667 / GB-second |

A request that takes 5 s on 2 GB memory = `5 × 2 × 0.0000166667 ≈ $0.000167`. At 100k requests/day = ~$500/month — **comparable to Fargate** at modest traffic but more variable. Lambda makes more sense if traffic is < 10k/day; Fargate wins if it's sustained.

### AgentCore Runtime

Pricing scales with concurrent sessions and time-in-session. AWS lists
agent-runtime-hour and CPU-time billing dimensions; figure on the order
of Fargate-equivalent for steady-state workloads, with the upside that
AgentCore handles auth, identity, and memory cohort routing for free.
Re-cost when ADR-0003 is implemented and IaC lands.

---

## Networking

### Internet-facing path (ALB → Fargate or AgentCore)

| Component | Monthly |
|---|---|
| ALB base | ~$22 |
| LCU-hour at modest traffic | ~$5–25 |
| Data transfer out (1 GB/day = ~30 GB/mo) | ~$3 |

### NAT Gateway (if Fargate runs in private subnets)

| Component | Monthly |
|---|---|
| NAT Gateway hourly (per AZ) | ~$32/AZ |
| Data processing | $0.045 / GB |

A 2-AZ deployment with 30 GB/mo egress = `64 + 1.35 = ~$65/month`.

### Bedrock data transfer

Bedrock calls go over AWS-internal network when the worker and the
Bedrock endpoint are in the same region. **No VPC endpoint required**
for cross-region inference — but consider one for `bedrock-runtime` if
you need to keep traffic off the public internet.

| Component | Monthly |
|---|---|
| VPC Interface Endpoint for `bedrock-runtime` | ~$7.30 + $0.01/GB |

---

## Observability

| Component | Price | At a 100k req/day workload |
|---|---|---|
| CloudWatch Logs ingestion | $0.50 / GB | ~$15/mo (assuming ~1 GB/day at INFO level) |
| CloudWatch Logs storage | $0.03 / GB-mo | ~$1/mo |
| CloudWatch Metrics (custom) | $0.30 / metric-mo | ~$10/mo (a few dozen counters/histograms) |
| X-Ray traces | $5 / 1M traces | ~$15/mo (sampled at 100%) or ~$1.50/mo (sampled at 10%) |
| OTLP exporter (if shipping to Datadog/Honeycomb/etc.) | varies | depends on vendor |

**Sampling matters.** For a 100k req/day workload at 100% trace sampling,
X-Ray itself is ~$15/mo; at 10% sampling, it's $1.50/mo. The default
`ParentBased` sampler in `telemetry.py` keeps 100%; consider lowering to
10% in prod via an explicit sampling-config setting (Phase D, Roadmap
gap #12).

---

## Storage

| Component | Price | Footprint |
|---|---|---|
| ECR (container images) | $0.10 / GB-mo | ~$0.02/mo (one ~200 MB image) |
| S3 (raw upload storage, **if persisted**) | $0.023 / GB-mo standard | depends on retention policy |

**Note**: today the service does not persist `document_text` or uploads.
Adding S3-side persistence (for e.g. an audit trail) would add storage
cost proportional to retention.

---

## Secrets and crypto

| Component | Price | Footprint |
|---|---|---|
| Secrets Manager | $0.40 / secret-mo + $0.05 / 10K API calls | ~$1.20/mo for ~3 secrets (API keys, JWT signing, OTLP token) |
| KMS (one CMK for Secrets Manager / S3 SSE-KMS) | $1 / key-mo + $0.03 / 10K requests | ~$1.50/mo |

---

## Total monthly estimates (rough)

For a **mixed-workload prod deployment at 10,000 req/day** (80% text /
20% vision) on **ECS Fargate × 2 tasks**:

| Bucket | Monthly |
|---|---|
| Bedrock invocations | ~$10,000 |
| ECS Fargate (2 tasks 24×7) | ~$72 |
| ALB | ~$30 |
| NAT Gateway (2 AZ) | ~$65 |
| CloudWatch + X-Ray + custom metrics | ~$30 |
| Storage / Secrets / KMS | ~$3 |
| **Total** | **~$10,200/mo** |

**Bedrock dominates by two orders of magnitude.** Operational work that
reduces inference cost (model swaps, prompt-token reduction, caching of
identical document hashes, retry-budget tightening, conditional
grounding) has the highest leverage. Infrastructure cost
optimisation (Fargate sizing, NAT vs. VPC endpoints, sampling rates)
matters but is in the ~$200/month range — worth doing once Bedrock is
right.

For a **dev / staging env at ~50 req/day** on a single Fargate task:

| Bucket | Monthly |
|---|---|
| Bedrock invocations (mixed) | ~$50 |
| ECS Fargate (1 task) | ~$36 |
| ALB | ~$22 |
| NAT (1 AZ if private; can also use public subnets in dev) | ~$32 or $0 |
| Observability | ~$5 |
| **Total dev / staging** | **~$95–145/mo** |

---

## Cost levers (in descending order of impact)

1. **Model selection.** Swap the default to **Claude Haiku 4.5** for
   schemas that don't need Sonnet's reasoning depth. Haiku is roughly
   $0.25 input / $1.25 output per 1M tokens — **~12× cheaper** than
   Sonnet. Set `BEDROCK_MODEL_ID=us.anthropic.claude-haiku-4-5-20251001-v1:0`
   per deployment. The eval harness (Phase D5, deferred) is the right
   place to measure whether Haiku meets each schema's accuracy bar.
2. **Conditional vision grounding.** Today every `/extract/document`
   image extraction does grounding (doubles per-call cost). Once Phase
   D5 produces calibrated confidence-vs-accuracy curves, grounding can
   be skipped when first-pass `overall_confidence` is reliably high —
   would cut vision cost by ~40% on the happy path.
3. **Retry-budget tightening.** Drop `max_retries` from 2 to 1
   per-deployment via the constructor parameter. Halves the worst-case
   ceiling on `/extract` and removes one extract + one grounding cycle
   from the vision worst case. ADR-0009 explicitly accepts this is an
   operator knob.
4. **Document-hash caching.** Identical documents (same SHA256) almost
   always extract to identical results. A small Redis or DynamoDB
   cache keyed on `(schema_name, schema_version, sha256(document))`
   would short-circuit Bedrock for re-submissions. Out of scope today;
   meaningful win if ≥ 5% of traffic is duplicate.
5. **Prompt-token pruning.** Each schema's prompt carries every field's
   description verbatim. Long descriptions add input-token cost on
   every call. A schema-side prompt-budget audit (cap descriptions at
   ~50 chars) would shave ~10% off input cost.
6. **Sampling rate.** Trace sampling at 10% in prod cuts X-Ray cost
   ~10× with negligible observability loss for SRE workflows. Roadmap
   gap #12.
7. **VPC endpoint for `bedrock-runtime`** if NAT data-processing cost
   becomes meaningful (~$0.045/GB through NAT vs. ~$0.01/GB through a
   VPC endpoint). Marginal at most workloads.

---

## Things this doc does NOT cost

- **PagerDuty / Opsgenie subscription.** TODO; pricing depends on
  contract tier (typically $20–40/user/month for the on-call plan).
  Out of scope today: the paging *policy* (severity matrix, escalation
  chain) is documented in `docs/runbooks/oncall.md`, but the *wiring*
  (`pagerduty_integration_key`, `sns_alarm_topic_arn`,
  `escalation_email_list` Terraform variables) is deferred — see
  `docs/deployment-variables.md` § 3 and Roadmap Critical #3.
- **Synthetic monitoring** (route53 health-check, external uptime
  probe). ~$15–50/month depending on vendor. Not yet wired.
- **CI minutes.** GitHub Actions ubuntu-latest runners are free for
  public repos; for private repos, the existing CI workflow set
  (lint + test + docs + load-test smoke) runs in roughly 5 minutes per
  PR. At GitHub's $0.008/minute private-repo rate, ~$1/PR. Not a
  meaningful line.
- **Eval-harness inference.** Phase D5 deferred. When it lands, expect
  ~$0.05/labelled fixture on Sonnet × hundreds of fixtures × runs per
  release = a few dollars per CI eval run.

---

## TODO

- **PagerDuty / Opsgenie wiring** — deferred. When provisioned, add
  the subscription cost as a recurring line. The integration steps are
  documented in `docs/runbooks/oncall.md`; the SNS-topic and
  service-key wiring is out of scope until an actual on-call rotation
  is set up.
- **Eval harness cost line** — write once Phase D5 is implemented.
- **AgentCore Runtime cost reconciliation** — re-cost compute against
  AgentCore once ADR-0003 is implemented; current Fargate numbers are
  the planning baseline.
