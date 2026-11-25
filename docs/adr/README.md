# Architecture Decision Records

Format: context → decision → consequences. Short on purpose.

## ADR-0001: Qdrant for hybrid (dense + sparse) retrieval
**Context.** We need BM25 and vector search, filters, tenancy and quantization, preferably in one engine.
**Decision.**
- Use Qdrant with named vectors: `dense` (cosine, int8 scalar quantization with rescoring) and `bm25` (sparse with `Modifier.IDF`).
- Fuse dense and sparse results client-side with RRF, so hit-testing can show each stage's ranks.

**Consequences.**
- One datastore to operate.
- IDF is maintained by the engine.
- Benchmark against pgvector + ParadeDB BM25 before production scale.

## ADR-0002: BM25 implemented as client-side sparse vectors
**Decision.** Use a stemmed tokenizer that keeps identifier-like tokens (`E-1042`, `v3.8.1`) verbatim, CRC32 term ids, and BM25 term-frequency saturation on documents. Qdrant applies IDF at query time.
**Consequences.**
- No vocabulary file and no model download.
- Results are deterministic across processes.
- Exact-term recall is preserved, which is the main reason hybrid search beats dense-only.

## ADR-0003: Structure-aware chunking with templates and parent-child chunks
**Decision.**
- Headings start chunks, tables are atomic, overlap stays inside a section.
- Templates are chosen per document type, and parent chunks support small-to-big retrieval.

**Consequences.**
- Better precision and citations that map to sections and pages.
- Templates are tuned with the eval harness rather than guessed.

## ADR-0004: Contextual enrichment is asynchronous and prompt-cached
**Decision.**
- A document becomes READY, and therefore searchable, before enrichment runs.
- Enrichment runs on the `light` queue with the document in a byte-stable system prompt.
- Chunks that already carry enough context are skipped.

**Consequences.**
- No added time-to-first-answer.
- Enrichment cost is dominated by cache reads.
- The Batch API can be used for backfills.

## ADR-0005: Build a gateway rather than adopt LiteLLM or Portkey
**Context.** The gateway is on every request's critical path, and its behaviour (cooldowns, fallbacks, accounting) is the subject of this project.
**Decision.**
- Write a focused gateway that speaks the OpenAI dialect.
- Use Portkey-style nested strategy trees and LiteLLM-style per-error retry policies and cooldowns, with state in Redis.

**Consequences.**
- We own the code.
- Clients use standard SDKs, so the gateway can be swapped for LiteLLM later.

## ADR-0006: One OpenAI-compatible adapter for OpenAI, Gemini, xAI, Ollama and vLLM; native Anthropic adapter
**Decision.** Use the compatibility layers instead of five SDK adapters, and a native adapter for Anthropic (prompt-cache breakpoints, structured outputs, refusal handling).
**Consequences.** Less code. Gemini-only features such as explicit context caching are out of scope.

## ADR-0007: Three cache layers with explicit invalidation
**Decision.** Use provider prompt caching (a stable prefix), an exact cache (Redis), and a semantic cache (Qdrant: short conversations, no tools, namespaced by tenant, route and system-prompt hash, embedding model stored per entry).
**Consequences.**
- Tag-based invalidation (`doc:<id>`) on document change or delete.
- Answer-cache entries are written only when citations verify.

## ADR-0008: Spend ledger written asynchronously
**Decision.** Push records to Redis in the request path; a background flusher batch-inserts into Postgres and re-queues on failure.
**Consequences.**
- No database latency or outage on the request path.
- The ledger is eventually consistent (about 2 s).

## ADR-0009: Memory as add-only extraction plus background consolidation
**Decision.**
- The write path is add-only.
- Consolidation deduplicates and invalidates facts bi-temporally; nothing is deleted.
- Facts keep provenance links, and users can view and erase them.

**Consequences.**
- Cheap and safe in the hot path.
- Some memory staleness until consolidation runs (nightly or on a trigger).

## ADR-0010: One Qdrant collection per Atlas collection, addressed through an alias
**Decision.** Use physical collections `atlas_<id>__vN` behind the alias `atlas_<id>`.
**Consequences.**
- Atomic blue/green re-index swaps and clean deletion.
- At very large tenant counts, migrate to a shared collection with `is_tenant` payload partitioning.

## ADR-0011: pdfplumber by default, Docling optional
**Decision.** The default image uses pdfplumber (MIT, small, keeps bounding boxes). Docling (layout models, OCR, PyTorch) is a build argument.
**Consequences.** A small default image; scanned PDFs need the Docling build.

## ADR-0012: RLS as the last line of tenant isolation
**Decision.** The app role is not the table owner. Per-transaction `set_config('app.tenant_id', …, true)`. Policies on every tenant table fail closed.
**Consequences.**
- A missing `WHERE tenant_id` cannot leak data.
- Admin paths (key lookup, bootstrap) use the owner role explicitly.
