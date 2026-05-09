# On-call runbook

This is the entry point for an on-call engineer paged for `bedrock-strands-agent`.
For a specific symptom, jump to the matching runbook below; for an unfamiliar
incident, work the **Quick triage** list first.

## Paging policy

| Severity | Trigger                                                                                              | Response time |
| -------- | ---------------------------------------------------------------------------------------------------- | ------------- |
| SEV-1    | Service down (availability < 99% over 5 min) or 100% extraction failure                              | 15 min        |
| SEV-2    | Sustained SLO breach (e.g. p95 > 3 s for 30 min, error rate > 5% for 15 min)                         | 30 min        |
| SEV-3    | Bedrock retry rate > 5%, single-tenant elevated 4xx, alarm-only metric drift                         | Next-business |

The PagerDuty/Opsgenie service is `bedrock-strands-agent` (escalation policy:
primary on-call → secondary → engineering manager).

## Specific runbooks

| Symptom                                                            | Runbook                                                                |
| ------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| Bedrock `InvocationThrottles` rising, retry-rate alarm             | [`bedrock-throttling.md`](./bedrock-throttling.md)                     |
| `/extract` 502 spike, "extraction failed" log lines                | [`extraction-error-spike.md`](./extraction-error-spike.md)             |
| 401 spike, `invalid or missing API key/JWT` in logs                | [`auth-misconfig.md`](./auth-misconfig.md)                             |

## Quick triage

Run these first before drilling into a specific runbook.

1. **Status page**:

   ```bash
   curl -fsS https://<gateway-id>.../health
   ```

2. **CloudWatch dashboards** (Phase B): `BedrockStrandsAgent-{env}` —
   look at the *Service health* panel for request rate / error rate / p95.

3. **Recent deploys**: check `gh run list --workflow=deploy.yml --limit 5`.
   If the incident started inside the last hour, suspect the most recent tag
   first.

4. **Bedrock console**: model invocations + throttle counts in the
   `bedrock-runtime` panel for `BEDROCK_MODEL_ID`.

5. **AgentCore Memory write failures** (Phase B): the
   `bedrock_agentcore.memory.create_event_failures` CloudWatch metric. A
   non-zero reading means audit writes are failing — extraction itself may
   still be healthy.

## Communication

- Open an incident channel `#inc-<yyyy-mm-dd>-<short-description>` in Slack.
- Post an initial summary within 5 min of paging: severity, suspected scope,
  next check time.
- Use `gh issue create -t "incident: ..."` to land the postmortem in the
  repo's `incidents` label after recovery.

## Rollback

If a new release is suspect, the fastest mitigation is image rollback in
AgentCore Runtime (no infra change):

```bash
cd infra/tofu/envs/prod
tofu apply -var image_tag=<previous-sha>
```

The full procedure (with verification + state lock release) lives in
`infra/tofu/README.md`. Do **not** roll back if the suspected bug is in a
client and the agent is healthy — talk to the calling team first.
