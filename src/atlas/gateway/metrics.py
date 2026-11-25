"""Prometheus metrics. Labels are deliberately low-cardinality: no tenant or user ids
(those belong in traces/ClickHouse, not in a TSDB)."""

from prometheus_client import Counter, Histogram

REQUESTS = Counter("atlas_gateway_requests_total", "Gateway requests",
                   ["route", "provider", "status", "cache"])
ATTEMPTS = Counter("atlas_gateway_attempts_total", "Upstream attempts", ["provider", "outcome"])
LATENCY = Histogram("atlas_gateway_latency_seconds", "End-to-end latency", ["route"],
                    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 64))
TTFT = Histogram("atlas_gateway_ttft_seconds", "Time to first token (streaming)", ["route"],
                 buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 4, 8))
OVERHEAD = Histogram("atlas_gateway_overhead_seconds", "Gateway-added latency (total - upstream)", ["route"],
                     buckets=(0.0005, 0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1))
TOKENS = Counter("atlas_gateway_tokens_total", "Tokens by bucket", ["provider", "bucket"])
COST = Counter("atlas_gateway_cost_usd_total", "Spend in USD", ["provider"])
SAVED = Counter("atlas_gateway_saved_usd_total", "USD saved by caches", ["cache"])
COOLDOWNS = Counter("atlas_gateway_cooldowns_total", "Targets put in cooldown", ["target"])
