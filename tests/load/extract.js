// k6 load test for the /extract endpoint.
//
// Three scenarios; which run is controlled by K6_PROFILE:
//   ci    — 10 RPS for 30s (intended for CI against the stub-mode app)
//   smoke — 5 RPS for 2m  (manual / release-time regression check)
//   soak  — 50 RPS for 5m (validates p95 latency + error rate against staging)
//
// Default profile (env unset) is `full` = smoke + soak, the original
// release-time behaviour. Set K6_PROFILE=ci to run only the CI scenario.
//
// Usage:
//   K6_PROFILE=ci  BASE_URL=http://127.0.0.1:8765 k6 run tests/load/extract.js
//   make load-test                              # local: BASE_URL=http://localhost:8000
//   BASE_URL=https://gw.example.com \
//     API_KEY=$(...) k6 run tests/load/extract.js
//
// Thresholds match the Phase A SLOs (docs/slos.md): p95 < 3s, error rate < 1%.

import http from 'k6/http';
import { check } from 'k6';
import { SharedArray } from 'k6/data';

const payloads = new SharedArray('payloads', () =>
  JSON.parse(open('./payloads/sample_form.json'))
);
const BASE = __ENV.BASE_URL || 'http://localhost:8000';
const API_KEY = __ENV.API_KEY || '';
const PROFILE = (__ENV.K6_PROFILE || 'full').toLowerCase();

const ALL_SCENARIOS = {
  ci: {
    executor: 'constant-arrival-rate',
    rate: 10,
    timeUnit: '1s',
    duration: '30s',
    preAllocatedVUs: 10,
    maxVUs: 20,
    exec: 'extract',
  },
  smoke: {
    executor: 'constant-arrival-rate',
    rate: 5,
    timeUnit: '1s',
    duration: '2m',
    preAllocatedVUs: 10,
    maxVUs: 20,
    exec: 'extract',
  },
  soak: {
    executor: 'constant-arrival-rate',
    rate: 50,
    timeUnit: '1s',
    duration: '5m',
    preAllocatedVUs: 80,
    maxVUs: 150,
    exec: 'extract',
    startTime: '2m30s',
  },
};

const PROFILE_TO_SCENARIO_NAMES = {
  ci: ['ci'],
  smoke: ['smoke'],
  soak: ['soak'],
  full: ['smoke', 'soak'],
};

if (!PROFILE_TO_SCENARIO_NAMES[PROFILE]) {
  // Fail loud on a typo. The previous silent fallback to `full` would have
  // silently run a 7m30s 50-RPS soak against whatever BASE_URL pointed at —
  // including real Bedrock if the operator was mid-debug against staging.
  throw new Error(
    `unknown K6_PROFILE='${PROFILE}'; valid: ${Object.keys(PROFILE_TO_SCENARIO_NAMES).join(', ')}`,
  );
}
const selected = PROFILE_TO_SCENARIO_NAMES[PROFILE];
const scenarios = {};
for (const name of selected) {
  scenarios[name] = ALL_SCENARIOS[name];
}

export const options = {
  scenarios,
  thresholds: {
    // Production SLO from docs/slos.md applies across all scenarios.
    http_req_duration: ['p(95)<3000'],
    // Tight scenario-scoped bound for the CI gate. The stub backend should
    // run far below this; treating any slowdown past 500ms as a regression
    // catches changes that the 3000ms global ceiling would absorb.
    'http_req_duration{scenario:ci}': ['p(95)<500'],
    http_req_failed: ['rate<0.01'],
  },
};

export function extract() {
  const body = payloads[__ITER % payloads.length];
  const headers = { 'Content-Type': 'application/json' };
  if (API_KEY) headers['X-API-Key'] = API_KEY;
  const res = http.post(`${BASE}/extract`, JSON.stringify(body), { headers });
  check(res, {
    'status 200': (r) => r.status === 200,
    'has fields[]': (r) => {
      try {
        return Array.isArray(r.json('fields'));
      } catch {
        return false;
      }
    },
  });
}
