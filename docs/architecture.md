# Atlas Architecture

> Diagrams: [`docs/diagrams/atlas.drawio`](diagrams/atlas.drawio) has 10 pages: system, gateway pipeline, ingestion,
> query flow, memory, deep research, data model, evaluation, deployment and module map. Regenerate it with `make diagrams`.

## 1. Goals and non-goals

| Goals | Non-goals |
|---|---|
| Cited, verifiable answers over private documents | Training or fine-tuning models |
| Measurable quality (CI eval gate) and measurable cost and latency | A general web search engine |
| Multi-tenant isolation with document ACLs | Building a vector database |
| Provider-independent LLM access with failover | UI polish (the API is the product) |

## 2. Services (one image, several roles)

| Service | Role | Scales on |
|---|---|---|
| `api` | Auth, tenancy, KB CRUD, query orchestration, SSE | CPU / RPS |
| `gateway` | OpenAI-compatible LLM gateway: routing, failover, caches, limits, budgets, async spend ledger | RPS |
| `worker-ingest` | fetch → parse → chunk → embed → index (queue `atlas:ingest`) | queue depth |
| `worker-light` | contextual enrichment, memory extraction, summaries, crons (`atlas:light`) | queue depth |
| `worker-research` | LangGraph deep research (`atlas:research`) | queue depth |
| `mcp` | MCP server exposing KB tools | n/a |
| `mockllm` | deterministic OpenAI-compatible LLM for CI, chaos tests and key-less demos | n/a |
| `tei-embed`, `tei-rerank` | HF Text-Embeddings-Inference model servers | GPU/CPU |
| Postgres, Qdrant, Redis, MinIO | metadata + RLS, vectors, queues/limits/caches, artifacts | n/a |

## 3. Key flows

### 3.1 Ingestion (`src/atlas/ingestion/pipeline.py`)
`FETCH → PARSE → CHUNK → EMBED → INDEX → READY`, then `ENRICH` asynchronously.
- **Idempotent and resumable.** Each stage writes a `document_stages` row. A queue job id (`ingest:<doc>:<version>`) deduplicates enqueues, and a cron re-queues stuck documents.
- **Cache at every expensive step:**
  - Re-uploading identical content does nothing (content hash).
  - Parse output is cached in S3 under `sha256(content, parser, pipeline_version)`.
  - Embeddings are cached in Redis under `(model, input type, text hash)`, so a new version of a document only pays to embed the chunks that changed.
- **Zero-downtime updates.** The new version's points are upserted first and stale versions deleted afterwards, so a reader never sees an empty document.
- **Chunking.** Structure-aware with per-type templates (`config/chunking.yaml`). Headings start new chunks, tables are atomic, overlap stays within a section, and parent chunks support small-to-big retrieval. Every chunk keeps page numbers and bounding boxes.
- **Contextual retrieval.** A cheap model writes one or two lines of context per chunk. The document sits in a byte-stable system prompt, so per-chunk calls are served largely from the provider prompt cache. A document summary feeds a summary index for "global" questions.

### 3.2 Query (`src/atlas/generation/pipeline.py`)
1. Load the memory window and summary. In parallel, fetch the user's relevant long-term facts.
2. **Plan.** Short first-turn questions skip the LLM via a heuristic. Otherwise one `fast` structured call both classifies intent (lookup, multi-hop, global, chit-chat) and condenses the history into a standalone query.
3. **Answer cache** (first turns only). Semantic lookup scoped by tenant, collection set, ACL groups and prompt version.
4. **Hybrid retrieval.**
   - Dense and BM25 searches run concurrently, with the tenant and ACL filter applied inside the query.
   - Results are combined with RRF.
   - Reranking is adaptive: it is skipped when both retrievers rank the same chunk first.
   - Multi-hop questions retrieve per sub-query and fuse the results.
5. **Abstain without an LLM call** when evidence is missing or the rerank score is below threshold.
6. **Context assembly.** Deduplicate, expand to parent chunks when the budget allows, merge adjacent chunks, number the sources, stop at the token budget.
7. **Stream the answer** through the gateway. The system prompt is byte-stable, so it stays in the provider cache. Answers are tagged with their source documents for cache invalidation.
8. **Verify citations.** Check each cited sentence against its source (lexical, free) and flag invalid markers and uncited factual sentences. The LLM judge is used for evals.
9. **Persist** the turn, then enqueue memory extraction and summary rolling (best-effort, never blocks the answer).

### 3.3 Gateway (`src/atlas/gateway/`)
Request path: rate limit (Redis Lua token bucket, RPM and estimated TPM) → budget (key/day, tenant/month; the soft limit downgrades the route) → exact cache → semantic cache → strategy tree (`fallback` / `loadbalance` / `latency`) → attempts.

Each attempt:
- Retries depend on the error class. Retry-after is honoured, and a long retry-after means failing over instead of sleeping.
- Failures count towards a Redis-shared cooldown.
- Context-window and content-policy errors switch to their own fallback lists.
- A 400 fails fast.

Streaming fails over only before the first token. Usage is normalised into non-overlapping buckets so cost is never double-counted, priced from a versioned table, and pushed to a Redis queue that a background task batch-inserts into Postgres. Spend logs are never written in the request path.

### 3.4 Memory (`src/atlas/memory/`)
| Tier | Store | Write | Read |
|---|---|---|---|
| Working | `messages` + `conversations.summary` | every turn; background summary roll | token-bounded window + summary |
| Long-term user facts | `memory_facts` (bi-temporal) + Qdrant `atlas_memory` | add-only extraction (cheap model, sensitive data excluded) | top-k relevant, valid only |
| Consolidation | the same tables | nightly / triggered: deduplicate (cosine ≥ 0.92), LLM supersede decisions → `invalid_at`, `superseded_by` | n/a |

Users can list and erase their memories (`GET/DELETE /v1/memory`), and every fact links to the message it came from.

### 3.5 Deep research (`src/atlas/research/`)
- **Shape:** a LangGraph `StateGraph`: planner → `Send` fan-out to researchers → writer → reviewer, with a bounded loop.
- **Durability:** a Postgres checkpointer (`thread_id` = job id) lets a crashed job resume.
- **Effort levels** set the fan-out, the number of review loops and the budget.
- **Budget:** when the budget runs out, the job produces a labelled partial report.
- **Progress:** pushed to a Redis list (replayable) and pub/sub, streamed over SSE.

## 4. Data and security
- **Tenant isolation, at three layers:**
  - Postgres RLS. The app role does not own the tables, and `app.tenant_id` is set per transaction, so an unset tenant fails closed.
  - A mandatory tenant filter, injected server-side, on every Qdrant query.
  - Tenant-scoped cache keys.
- **Identity and ACLs.** ACL groups come from the user record or from an `impersonate`-scoped trusted backend. Clients cannot widen their own groups.
- **Untrusted content.** Sources are wrapped as data and the prompt says to ignore instructions inside them.
- **Uploads.** MIME type comes from magic bytes, size is limited, and parsers have timeouts and a memory cap on the worker.
- **URL ingestion** is protected against SSRF: private, loopback, link-local and metadata IPs are blocked, and redirects are re-checked on every hop.
- **Secrets.** API keys are stored as SHA-256 hashes, logs redact secrets, and prompt and response bodies are not exported to telemetry by default.
- **Bootstrap.** Setting up a tenant needs a deployment token; there is no default admin login.

## 5. Observability
- **Traces:** OTel spans per stage using the GenAI semantic conventions. The attribute names live in one class, because the spec moved repositories in 2026 and is still at Development stability.
- **Metrics:** Prometheus covers the HTTP API and the gateway (requests, latency, TTFT, gateway overhead, tokens, cost, cache savings, cooldowns). Labels are low-cardinality only.
- **Usage API:** `/v1/usage` reports spend, tokens, cache hit rate and savings per route and model.

## 6. SLOs (validated with `loadtest/`, see `docs/IMPACT.md`)
| SLO | Target |
|---|---|
| Query TTFT p95 (standard mode) | < 1.5 s |
| Retrieval p95 (hybrid + adaptive rerank) | < 250 ms |
| Gateway-added overhead p99 | < 10 ms |
| Availability during a single-provider outage | > 99% |
| Faithfulness (support ratio), golden set | ≥ 0.90, gated in CI |

## 7. Known trade-offs
- **One Qdrant collection per Atlas collection** gives clean blue/green alias swaps. At thousands of tenants, switch to a shared collection with `is_tenant` partitioning (ADR-0010).
- **API-key lookups are cached for 60 s**, so a revoked key keeps working for up to a minute.
- **Lexical citation verification** is cheap but approximate. The LLM entailment check is used in evals and can be sampled online.
