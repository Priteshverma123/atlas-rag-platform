# Atlas: Production RAG Platform

Atlas is a multi-tenant knowledge platform. You upload PDFs, DOCX, slides or web pages and get **cited,
verified answers**, with hybrid retrieval, user memory, a multi-agent research mode and a **multi-provider LLM
gateway**, all running in Docker and measured by a CI evaluation gate.

![Chat with cited, verified answers over the RBI Cybersecurity Directions](docs/images/screenshots/05-chat-cited-answers.png)

## Architecture

```mermaid
flowchart LR
    U[Atlas Console<br/>React + Vite] -->|/api| API
    C[REST / MCP clients] --> API
    subgraph Core[atlas-api]
        API[FastAPI] --> G1[guardrails<br/>injection · PII · secrets]
        G1 --> MEM[memory<br/>buffer + long-term facts]
        G1 --> RET[hybrid retrieval<br/>dense ‖ BM25 → RRF → adaptive rerank]
        RET --> CTX[context assembly<br/>small-to-big, token budget]
        CTX --> CIT[citation verification]
    end
    API -->|arq queues| W[workers<br/>ingest · light · research]
    W --> TEI[TEI<br/>embed · rerank]
    RET --> TEI
    RET --> QD[(Qdrant<br/>dense + sparse)]
    W --> QD
    W --> S3[(MinIO)]
    API --> PG[(Postgres + RLS)]
    CTX --> GW[atlas-gateway<br/>fallback · cache · limits · budgets]
    W --> GW
    GW --> P[Anthropic · OpenAI · Gemini · Ollama · mock]
    API -. OTLP .-> PULSE[Pulse<br/>self-hosted tracing]
    GW -. OTLP .-> PULSE
    W -. OTLP .-> PULSE
    PULSE --> PG
    U -->|/pulse| PULSE
```

The ingestion path is upload → MinIO → parse → structure-aware chunking → embed (cached) → Qdrant upsert with alias blue/green, and each stage's status is visible in the console.
Plain-language flow and user-flow diagrams are in [`docs/diagrams/atlas-flows.drawio`](docs/diagrams/atlas-flows.drawio) (7 pages). Detailed technical diagrams are in [`docs/diagrams/atlas.drawio`](docs/diagrams/atlas.drawio) (10 pages). The design is described in [`docs/architecture.md`](docs/architecture.md) and the decisions in [`docs/adr/`](docs/adr/README.md).

## What's inside

| Area | Highlights |
|---|---|
| **LLM Gateway** | OpenAI-compatible; nested strategy trees (fallback / loadbalance / latency); per-error retry policy; Redis-shared cooldowns; context-window and content-policy fallback lists; TTFT failover for streams; exact + semantic + provider prompt caching; token-bucket RPM/TPM; key → tenant budgets with soft downgrade; versioned pricing with cache-token buckets; async spend ledger; Prometheus metrics |
| **Ingestion** | PDF (page + bbox), DOCX, PPTX, HTML/URL (SSRF-safe), Markdown; optional Docling; idempotent stages with per-stage status; parse / embedding caches; versioned re-ingest that only re-embeds changed chunks; contextual enrichment; summary index |
| **Hybrid search** | Qdrant dense (int8 + rescoring) + BM25 sparse (IDF), RRF, adaptive cross-encoder rerank, per-collection settings, ACL filtering inside the query, hit-testing API with per-stage ranks |
| **Answers** | Router + history condensation in one cheap call (skipped for simple first turns), abstention without an LLM call, small-to-big context under a token budget, streaming SSE, citation verification |
| **Memory** | Token-bounded buffer + rolling summary; long-term user facts with add-only extraction, bi-temporal consolidation and provenance; user-visible GET/DELETE |
| **Deep research** | LangGraph planner → parallel researchers → writer → reviewer; Postgres checkpoints; effort levels; budget guard with partial reports; live SSE progress |
| **Platform** | Postgres row-level security, scoped API keys, MCP server, OTel GenAI spans, k6 load tests, CI with SHA-pinned actions, Trivy + SBOM, path-filtered eval gate |

## Screenshots

These were captured from the running stack (Chromium driven by Playwright). The test document is the 56-page RBI Cybersecurity Directions PDF, uploaded through the console.

| | |
|---|---|
| ![Login](docs/images/screenshots/01-login.png) **Demo login**: one click creates a seeded workspace | ![Processing](docs/images/screenshots/02-collections-processing.png) **Upload**: the document moves through the pipeline |
| ![Ready](docs/images/screenshots/03-collections-ready.png) **Collection ready** | ![Pipeline](docs/images/screenshots/04-document-pipeline-chunks.png) **Per-stage pipeline status** and the chunks actually indexed |
| ![Chat](docs/images/screenshots/05-chat-cited-answers.png) **Cited answers** with section and page, citations verified | ![Guardrail](docs/images/screenshots/06-chat-guardrail-block.png) **Guardrail** blocks a prompt-injection attempt |
| ![Search](docs/images/screenshots/07-search-playground.png) **Search playground**: dense / sparse / fused ranks and latency per stage | |

## Quickstart: one command

```bash
cp .env.example .env
docker compose up -d --build --wait
open http://localhost:3000       # Atlas Console → "Start the demo"
```
The demo creates a workspace with sample company documents. From the console you can:
- upload your own files
- chat with cited answers
- open the search playground, deep research and your memory
- inspect every request in **Tracing** (trace tree, timeline, inputs and outputs, tokens, cost, scores)

**LLM safety switch.** Real providers are **off** until you set `ATLAS_LLM_LIVE=true` in `.env`. Until then, keys in `.env` are ignored and everything runs on a deterministic mock LLM, so nothing is spent. Then run `docker compose up -d` again.

| URL | What |
|---|---|
| `localhost:3000` | Atlas Console (UI) |
| `localhost:8000/docs` | Atlas REST API (OpenAPI) |
| `localhost:8090` | LLM gateway (OpenAI-compatible) |
| `localhost:8100/docs` | Pulse, self-hosted tracing API (OTLP ingest at `/v1/traces`) |

> If `docker compose` fails with `401 Unauthorized` from `auth.docker.io`, your Docker Hub login has expired.
> Sign in again in Docker Desktop, or run `docker logout`.

## Pulse: self-hosted tracing (reusable in any project)
`src/pulse` is a standalone observability kit with no Atlas imports:
- **SDK:** `pulse.init()`, `pulse.trace_context()`, `pulse.observe()`, and `@pulse.observed` for functions.
- **Server:** OTLP/HTTP ingest buffered in Redis, then written to Postgres, with traces aggregated from their spans.
- **Query API:** traces, observations, sessions, users, metrics and scores.

Atlas uses it to trace every request end to end:

`ask → guardrail.input → memory.recall ‖ route → retrieval.search (embed, rerank) → context.assemble → chat (gateway generation: model, tokens, cost) → guardrail.output → citations.verify (scores)`

It also traces ingestion jobs, enrichment, memory extraction and research agents.

```python
import pulse
pulse.init("my-service", endpoint="http://localhost:8100", api_key="pk-...")
with pulse.trace_context(user_id="u1", session_id="chat-1"), pulse.observe("ask", input=q) as obs:
    obs.set_output(answer)
```
Any OpenTelemetry-instrumented app (any language) can also send OTLP to Pulse.

## Quickstart: API only

```bash
make bootstrap                  # → {"tenant_id": ..., "api_key": "atl_..."}
export KEY=atl_...

# create a collection and add a document
CID=$(curl -s localhost:8000/v1/collections -H "Authorization: Bearer $KEY" \
      -H 'content-type: application/json' -d '{"name":"handbook"}' | jq -r .id)
curl -s localhost:8000/v1/collections/$CID/documents -H "Authorization: Bearer $KEY" -F file=@evals/corpus/kd200_manual.md

# ask (SSE stream: meta → sources → token… → done)
curl -N localhost:8000/v1/query -H "Authorization: Bearer $KEY" -H 'content-type: application/json' \
     -d "{\"question\":\"What does E-1042 mean?\",\"collection_ids\":[\"$CID\"]}"
```
The OpenAPI docs are at `http://localhost:8000/docs`. The gateway is at `:8090` and works with any OpenAI SDK (`model="answer"`). The MCP server is at `:8765`.

Optional profiles: `--profile ollama` (fully local LLM), `--profile observability` (OTel collector, Jaeger, Prometheus, Grafana). Build with `WITH_DOCLING=1` for layout-aware and OCR PDF parsing.

## API overview
`/v1/collections` · `/v1/collections/{id}/documents[/text|/url]` · `/v1/documents/{id}[/chunks|/reprocess]` ·
`/v1/search` (hit testing) · `/v1/query` (SSE or JSON) · `/v1/conversations` · `/v1/memory` · `/v1/research` (+ `/events`) ·
`/v1/usage` · `/v1/feedback` · `/v1/admin/{bootstrap,keys}` · gateway `/v1/chat/completions`, `/v1/models`

## Evaluation
```bash
make eval KEY=$KEY      # ingests evals/corpus, runs the retrieval ablation and the generation suite
```
The dataset is `evals/datasets/smoke.jsonl` (25 questions: exact-term, lookup, table, multi-hop, global, unanswerable). Relevance is labelled with *gold evidence strings*, not chunk ids, so labels survive re-chunking. CI runs the suite on the mock LLM for every PR that touches retrieval, generation, ingestion or config, and fails on regressions (`atlas-eval gate`).

## Results
Measured numbers live in [`docs/IMPACT.md`](docs/IMPACT.md), each with the command that produced it. Numbers are added only after they are measured.

Current status (mock LLM, CPU-only TEI on a laptop):
- **End-to-end smoke test:** `scripts/smoke_e2e.py` passes 19/20 checks. Abstention on an out-of-scope question still needs threshold calibration.
- **Unit tests:** 43/43 pass.
- **RBI golden dataset:** 50 questions with validated evidence in `evals/datasets/rbi_golden.jsonl`. The accuracy run with a real LLM judge is pending.
- **Known limit:** cross-encoder reranking on CPU takes several seconds per query. Use GPU TEI or a smaller reranker in production.

## Repository layout
```
src/atlas/
  api/         FastAPI app, auth, routers          gateway/   providers, engine, caches, limits, pricing, spend
  ingestion/   parsers, chunking, enrich, pipeline  retrieval/ planner, fusion, rerank, context, service
  generation/  prompts, citations, pipeline         memory/    conversation buffer, long-term facts
  research/    LangGraph graph + runner             kb/        domain service, object store
  index/       Qdrant store                         embeddings/ TEI client, BM25 encoder
  workers/     arq queues + crons                   evals/     metrics, runner, CI gate
  mcp_server/  MCP tools                            mockllm/   deterministic OpenAI-compatible LLM
config/  gateway.yaml · pricing.yaml · chunking.yaml      migrations/  Alembic (+ RLS)
docker/  Dockerfile · postgres init                       deploy/      observability configs
evals/   corpus · datasets · baselines · reports          loadtest/    k6
```

## Development
```bash
uv sync && make test lint typecheck
```
