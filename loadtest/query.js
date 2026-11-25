// End-to-end query load:  ATLAS_KEY=<api key> COLLECTION=<id> k6 run loadtest/query.js
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  scenarios: { steady: { executor: 'constant-arrival-rate', rate: 20, timeUnit: '1s', duration: '3m', preAllocatedVUs: 100 } },
  thresholds: { http_req_failed: ['rate<0.01'], 'http_req_duration{kind:query}': ['p(95)<3000'] },
};

const QUESTIONS = ['What does error code E-1042 mean?', 'What is the meal allowance in London?',
  'How quickly must a security incident be reported?', 'What was revenue in Q3 2026?'];

export default function () {
  const q = QUESTIONS[Math.floor(Math.random() * QUESTIONS.length)];
  const res = http.post(`${__ENV.API_URL || 'http://localhost:8000'}/v1/query`,
    JSON.stringify({ question: q, collection_ids: [__ENV.COLLECTION], stream: false }),
    { headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${__ENV.ATLAS_KEY}` }, tags: { kind: 'query' } });
  check(res, { 'status 200': (r) => r.status === 200 });
}
