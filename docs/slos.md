# Service Level Objectives

These targets are conservative defaults set in Phase A. They will be ratified
after one week of staging telemetry (see ROADMAP entry "SLO ratification").

## Targets

| SLO                          | Target            | Window | Source of truth                                                                                                       |
| ---------------------------- | ----------------- | ------ | --------------------------------------------------------------------------------------------------------------------- |
| Availability                 | 99.9%             | 30 d   | `http_requests_total{status!~"5.."}` / `http_requests_total` from `prometheus-fastapi-instrumentator` (`app.py:78`)   |
| Latency p95                  | < 3 s             | 5 m    | `http_request_duration_seconds` histogram                                                                             |
| Error rate                   | < 1%              | 5 m    | `http_requests_total{status=~"5.."}` / `http_requests_total`                                                          |
| Bedrock retry rate           | < 5%              | 1 h    | `bedrock.retry` span event count / `extraction.run` span count (X-Ray) — see `agent/bedrock_retry.py`                 |
| Extraction failure rate      | < 2%              | 5 m    | `http_requests_total{handler="/extract", status="502"}` / total                                                       |
| Extraction quality (Phase D) | citation_verified ≥ 90%, faithfulness ≥ 0.85 | 1 d | `extraction_citations_total{verified="true"}` rate; DeepEval `FaithfulnessMetric` from online eval (D8) |

## Error-budget burn alerts

Two-window burn-rate alerts per the
[Google SRE workbook](https://sre.google/workbook/alerting-on-slos/). For a
99.9% availability SLO across 30 d (43.2 m budget):

- **Fast burn**: 14.4× over 1 h — pages immediately. Budget will be exhausted
  in under 2 d at this rate.
- **Slow burn**: 6× over 6 h — ticket within the next business day. Budget
  exhausted in under 5 d.

Each alert fires an SNS notification (Phase B§B9) routed to PagerDuty/Opsgenie
(integration ARN to be filled — see `docs/runbooks/oncall.md`).

## Out of SLO (informational)

- Cost per extraction (`bedrock_token_cost_usd`) — tracked, not bounded.
- Tool-call count per extraction — tracked for performance regressions.
- Confidence distribution — tracked to spot prompt drift.

## Where each SLO is measured

| SLO                      | Local                 | Dev cloud                                              | Prod                                                |
| ------------------------ | --------------------- | ------------------------------------------------------ | --------------------------------------------------- |
| Availability             | n/a                   | CloudWatch metric `http_requests_total`                | same                                                |
| Latency p95              | k6 run (`make load-test`) | CloudWatch + X-Ray service map                     | same                                                |
| Error rate               | pytest                | CloudWatch metric                                      | same                                                |
| Bedrock retry rate       | `tests/test_bedrock_retry.py` | X-Ray segment annotations                       | same                                                |
| Extraction failure rate  | pytest                | CloudWatch metric                                      | same                                                |
| Extraction quality       | `make eval`           | Eval harness on dev gateway URL                        | Online eval sampling (D8) at `online_eval_rate=0.01` |

## Review cadence

Review SLOs quarterly. Bring metrics + alarm trace to the review; do not adjust
targets reactively from a single incident.
