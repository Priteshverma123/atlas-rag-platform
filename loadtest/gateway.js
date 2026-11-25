// Gateway overhead benchmark: drive the gateway against the mock provider, then read
// atlas_gateway_overhead_seconds (gateway-added latency) from /metrics.
//   ATLAS_KEY=<internal key> k6 run loadtest/gateway.js
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: {
    ramp: {
      executor: 'ramping-arrival-rate', startRate: 50, timeUnit: '1s', preAllocatedVUs: 200,
      stages: [{ target: 200, duration: '1m' }, { target: 500, duration: '2m' }, { target: 0, duration: '30s' }],
    },
  },
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(99)<500'] },
};

const URL = __ENV.GATEWAY_URL || 'http://localhost:8090';
const HEADERS = {
  'Content-Type': 'application/json',
  Authorization: `Bearer ${__ENV.ATLAS_KEY || 'change-me-internal'}`,
  'x-atlas-cache': 'bypass',
};

export default function () {
  const body = JSON.stringify({ model: 'mock/atlas-mock', max_tokens: 64,
    messages: [{ role: 'user', content: `ping ${Math.random()}` }] });
  const res = http.post(`${URL}/v1/chat/completions`, body, { headers: HEADERS });
  check(res, { 'status 200': (r) => r.status === 200 });
}
