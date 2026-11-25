# Impact log

Every optimisation is recorded here with a measured before/after and the quality check that
accompanied it. **No number goes on a resume until it appears in this table with a reproducible
command.**

| Change | Metric | Before | After | How measured | Quality held? | Trade-off |
|---|---|---|---|---|---|---|
| Hybrid BM25 + dense (RRF) vs dense-only | recall@5 / nDCG@10 | _tbd_ | _tbd_ | `make eval` retrieval ablation | n/a | +1 parallel query |
| Adaptive rerank (skip when retrievers agree) | retrieval p95 / nDCG@10 | _tbd_ | _tbd_ | ablation + `rerank_rate` | nDCG change ≤ 0.01 | slight quality risk |
| Heuristic planner skip on first short turns | TTFT p95 | _tbd_ | _tbd_ | `loadtest/query.js` | answer correctness | misses rare reformulations |
| Context budget 6k → 3.5k tokens | $/query, TTFT | _tbd_ | _tbd_ | `/v1/usage` + eval | support ratio | fewer sources |
| Provider prompt caching (stable system prefix) | cache_read share, TTFT | _tbd_ | _tbd_ | `/v1/usage` cache_read | n/a | none |
| Exact + semantic answer cache | hit rate, $ saved | _tbd_ | _tbd_ | `/v1/usage` saved_usd | verified-only writes | staleness until invalidation |
| Abstain without LLM call | $ on unanswerable queries | _tbd_ | _tbd_ | eval abstention accuracy | false-abstention rate | threshold tuning |
| Gateway failover (chaos: primary 100% 503) | success rate | _tbd_ | _tbd_ | `mockllm /admin/chaos` + k6 | n/a | added latency on failover |
| Gateway overhead | p99 added latency | n/a | _tbd_ | `make load-gateway` + `atlas_gateway_overhead_seconds` | n/a | n/a |
| Embedding cache on re-ingest | embed calls avoided | _tbd_ | _tbd_ | stage stats `cache_hits` | n/a | Redis memory |
