// k6 load test for the /extract endpoint.
//
// Usage:
//   make load-test                              # local: BASE_URL=http://localhost:8000
//   BASE_URL=https://gw.example.com \
//     API_KEY=$(...) k6 run tests/load/extract.js
//
// Two scenarios run sequentially:
//   smoke — 5 RPS for 2 minutes (catches obvious regressions cheaply).
//   soak  — 50 RPS for 5 minutes (validates p95 latency and error rate).
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

export const options = {
  scenarios: {
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
  },
  thresholds: {
    http_req_duration: ['p(95)<3000'],
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
