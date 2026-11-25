"""Generates docs/diagrams/atlas.drawio (diagrams-as-code).

Run:  uv run python docs/diagrams/build_diagrams.py
Open the output in draw.io / diagrams.net or the VS Code "Draw.io Integration" extension.
"""

from pathlib import Path

from _drawio import Document

doc = Document()

# ---------------------------------------------------------------------------
# 1. System architecture (containers)
# ---------------------------------------------------------------------------
p = doc.page("1. System Architecture")
p.title("Atlas: System Architecture (containers)",
        "Every box below is a Docker Compose service. Solid = sync request, dashed = async/queue, dotted purple = telemetry.")

p.node("user", "End user /<br>demo UI", 30, 140, kind="client")
p.node("dev", "App developer<br>(SDK / REST)", 30, 230, kind="client")
p.node("relay", "Relay agents /<br>MCP clients", 30, 320, kind="client")

edge_zone = p.group("edge", "Edge", 220, 100, 220, 300, kind="zone")
p.node("api", "atlas-api<br><i>FastAPI · auth · tenancy · SSE</i>", 30, 40, 170, 70, kind="service", parent=edge_zone)
p.node("mcp", "atlas-mcp<br><i>MCP server (KB tools)</i>", 30, 180, 170, 60, kind="service", parent=edge_zone)

core = p.group("core", "Core services", 480, 100, 520, 300, kind="zone")
p.node("retr", "Retrieval engine<br>router · rewrite · hybrid · rerank · context budget", 20, 40, 230, 70, kind="component", parent=core)
p.node("gen", "Generation<br>answer · citations · verifier", 270, 40, 230, 70, kind="component", parent=core)
p.node("mem", "Memory<br>buffer · summaries · long-term facts", 20, 140, 230, 60, kind="component", parent=core)
p.node("res", "Deep Research<br>(LangGraph multi-agent)", 270, 140, 230, 60, kind="agent", parent=core)
p.node("kb", "KB services<br>collections · documents · chunks · ACL", 20, 220, 480, 50, kind="component", parent=core)

p.node("gw", "atlas-gateway<br><i>routing · fallback · cooldowns · caches · limits · budgets</i>", 1050, 140, 220, 80, kind="service")
prov = p.group("prov", "LLM providers", 1320, 80, 200, 330, kind="boundary")
for i, (k, lbl) in enumerate([("oai", "OpenAI"), ("ant", "Anthropic"), ("gem", "Google Gemini"), ("xai", "xAI Grok"),
                              ("oll", "Ollama / vLLM (local)"), ("mock", "mock-llm (CI/chaos)")]):
    p.node(k, lbl, 25, 35 + i * 48, 150, 38, kind="llm", parent=prov)

workers = p.group("workers", "Background workers (arq, split by queue)", 220, 450, 780, 120, kind="zone")
p.node("wi", "worker-ingest<br><i>parse · chunk · embed · index</i>", 20, 40, 170, 60, kind="worker", parent=workers)
p.node("we", "worker-light<br><i>contextual enrich · memory consolidation</i>", 210, 40, 200, 60, kind="worker", parent=workers)
p.node("wr", "worker-research<br><i>long-running research jobs</i>", 430, 40, 160, 60, kind="worker", parent=workers)
p.node("ws", "scheduler<br><i>cron: URL re-sync, eval</i>", 610, 40, 150, 60, kind="worker", parent=workers)

models = p.group("models", "Model servers (TEI)", 1050, 450, 220, 120, kind="zone")
p.node("tei_e", "tei-embed<br>bge-small / bge-m3", 20, 30, 180, 36, kind="llm", parent=models)
p.node("tei_r", "tei-rerank<br>bge-reranker", 20, 72, 180, 36, kind="llm", parent=models)

data = p.group("data", "Data plane", 220, 620, 1050, 130, kind="zone")
p.node("pg", "Postgres 16<br>metadata · RLS · usage · memory", 30, 35, 190, 80, kind="db", parent=data)
p.node("qd", "Qdrant<br>dense + BM25 sparse<br>semantic cache", 260, 35, 190, 80, kind="db", parent=data)
p.node("rd", "Redis<br>queues · rate limits · cooldowns · exact cache", 490, 35, 220, 80, kind="cache", parent=data)
p.node("s3", "MinIO (S3)<br>raw + parsed artifacts", 750, 35, 170, 80, kind="storage", parent=data)

p.node("otel", "OTel Collector → Lumen / Jaeger", 1320, 650, 200, 60, kind="external")

for s in ("user", "dev"):
    p.edge(s, "api", "HTTPS")
p.edge("relay", "mcp", "MCP")
p.edge("mcp", "api", "REST")
p.edge("api", "retr")
p.edge("retr", "gen")
p.edge("api", "kb")
p.edge("api", "mem")
p.edge("api", "res", "jobs")
p.edge("gen", "gw", "OpenAI-compatible")
p.edge("gw", "prov")
p.edge("kb", "wi", "enqueue", kind="async")
p.edge("wi", "we", "enqueue", kind="async")
p.edge("retr", "tei_e")
p.edge("retr", "tei_r")
p.edge("wi", "tei_e")
p.edge("retr", "qd", kind="data")
p.edge("wi", "qd", kind="data")
p.edge("wi", "s3", kind="data")
p.edge("kb", "pg", kind="data")
p.edge("gw", "rd", kind="data")
p.edge("gw", "otel", kind="telemetry")
p.legend(1320, 760)

# ---------------------------------------------------------------------------
# 2. LLM gateway request pipeline
# ---------------------------------------------------------------------------
p = doc.page("2. LLM Gateway Pipeline")
p.title("atlas-gateway: request pipeline",
        "Portkey-style nested strategy tree + LiteLLM-style per-error retry policy & cooldowns (state in Redis). Spend logs are never written in the request path.")
steps = [
    ("in", "POST /v1/chat/completions", "start"),
    ("auth", "Authenticate virtual key → tenant, user, route", "step"),
    ("rl", "Token-bucket check (RPM + estimated TPM)", "decision"),
    ("bud", "Budget check (key → user → tenant)", "decision"),
    ("ex", "Exact cache lookup (sha256 canonical request)", "decision"),
    ("sem", "Semantic cache lookup (if route enabled,<br>≤ 4 msgs, no tools)", "decision"),
    ("plan", "Resolve strategy tree<br>fallback → loadbalance → targets", "step"),
    ("cool", "Skip targets in cooldown", "step"),
    ("call", "Provider adapter call<br>(TTFT timeout, streaming)", "step"),
    ("err", "Classify error", "decision"),
    ("ok", "Normalize response + usage<br>(cache-read/write tokens, cost)", "step"),
    ("store", "Write caches · emit OTel span ·<br>enqueue spend record (async)", "step"),
    ("out", "Return / stream SSE", "end"),
]
y = 90
for k, lbl, kind in steps:
    h = 80 if kind == "decision" else 50
    w = 260 if kind == "decision" else 260
    p.node(k, lbl, 300, y, w, h, kind=kind)
    y += h + 25
order = [s[0] for s in steps]
for a, b in zip(order, order[1:]):
    if (a, b) == ("err", "ok"):
        continue
    p.edge(a, b, "success" if a == "call" else "", kind="flow")
p.edge("err", "ok", "no error", kind="flow")
p.node("r429", "429 Too Many Requests<br>(retry-after)", 650, 245, kind="end")
p.edge("rl", "r429", "over limit", kind="flow")
p.node("r402", "402/429 budget exceeded<br>or downgrade tier (soft limit)", 650, 345, 200, 50, kind="end")
p.edge("bud", "r402", "over budget", kind="flow")
p.node("hit", "Return cached response<br>(x-atlas-cache: exact|semantic)", 650, 450, 210, 50, kind="end")
p.edge("ex", "hit", "hit", kind="flow")
p.edge("sem", "hit", "hit ≥ threshold", kind="flow")
p.node("retry", "Retryable (429/5xx/timeout)?<br>backoff + jitter, honor retry-after;<br>record failure → maybe cooldown", 650, 860, 260, 80, kind="note")
p.node("fb", "Context-window error → context fallback list<br>Content-policy / refusal → policy fallback list<br>Auth/400 → fail fast (no retry)", 650, 960, 300, 70, kind="note")
p.edge("err", "retry", "retryable", kind="flow")
p.edge("err", "fb", "classified", kind="flow")
p.edge("retry", "cool", "next target", kind="async")
p.node("cfg", "<b>routes.yaml</b><br>rag-answer:<br>&nbsp; strategy: fallback<br>&nbsp; targets:<br>&nbsp;&nbsp; - loadbalance: [anthropic/sonnet, openai/gpt]<br>"
       "&nbsp;&nbsp; - local/llama<br>&nbsp; retry_policy: {RateLimit: 3, Timeout: 2}<br>&nbsp; cooldown: {allowed_fails: 3, seconds: 30}<br>"
       "&nbsp; cache: {exact: true, semantic: 0.95}", 1000, 90, 330, 190, kind="note")

# ---------------------------------------------------------------------------
# 3. Ingestion flow
# ---------------------------------------------------------------------------
p = doc.page("3. Ingestion Flow")
p.title("Ingestion: idempotent, resumable stage machine",
        "Each (doc_version, stage) is keyed by content hash + pipeline_version. Re-uploading unchanged content is a no-op; changed docs re-embed only changed chunks.")
lane_w = 1500
lanes = [("API", 90), ("worker-ingest", 230), ("worker-light", 400), ("Stores", 540)]
for name, yy in lanes:
    p.group(f"lane-{name}", name, 20, yy, lane_w, 130, kind="lane")
p.node("up", "POST document<br>(file | URL | text)", 60, 135, kind="start")
p.node("hash", "sha256(content)<br>dedupe / new version", 230, 130, 170, 60, kind="decision")
p.node("q", "enqueue ingest job<br>202 + job id", 430, 135, 150, 50, kind="queue")
p.node("fetch", "FETCHED<br>download / store raw", 60, 270, 150, 50, kind="step")
p.node("parse", "PARSED<br>pdfplumber | Docling | DOCX | HTML", 240, 270, 190, 50, kind="step")
p.node("chunk", "CHUNKED<br>template per doc type,<br>parent-child, page+bbox", 460, 265, 190, 60, kind="step")
p.node("diff", "diff chunk hashes vs<br>previous version", 680, 270, 170, 50, kind="decision")
p.node("emb", "EMBEDDED<br>dense (TEI) + BM25 sparse", 880, 270, 180, 50, kind="step")
p.node("idx", "INDEXED<br>upsert Qdrant, delete stale", 1090, 270, 180, 50, kind="step")
p.node("ready", "READY", 1300, 275, kind="end")
p.node("enr", "CONTEXTUALIZED (async)<br>cheap LLM writes chunk context<br>prompt-cached doc · Batch API", 880, 425, 230, 70, kind="worker")
p.node("reidx", "re-embed enriched chunks<br>(swap in place)", 1150, 435, 190, 50, kind="step")
p.node("pg", "Postgres: documents,<br>document_stages, chunks", 230, 565, 200, 70, kind="db")
p.node("s3", "MinIO: raw/, parsed/", 470, 565, 160, 70, kind="storage")
p.node("qd", "Qdrant collection<br>(alias → blue/green)", 1090, 565, 180, 70, kind="db")
p.node("fail", "FAILED(stage)<br>retry w/ backoff → DLQ", 680, 160, 170, 50, kind="end")
for a, b in [("up", "hash"), ("hash", "q"), ("fetch", "parse"), ("parse", "chunk"), ("chunk", "diff"), ("diff", "emb"),
             ("emb", "idx"), ("idx", "ready"), ("enr", "reidx")]:
    p.edge(a, b, kind="flow")
p.edge("q", "fetch", "arq", kind="async")
p.edge("idx", "enr", "enqueue", kind="async")
p.edge("reidx", "qd", kind="data")
p.edge("fetch", "s3", kind="data")
p.edge("hash", "pg", kind="data")
p.edge("idx", "qd", kind="data")
p.edge("parse", "fail", "error", kind="async")

# ---------------------------------------------------------------------------
# 4. Query flow
# ---------------------------------------------------------------------------
p = doc.page("4. Query Flow")
p.title("Query: memory-aware hybrid retrieval → cited, verified answer",
        "Each step is an OTel span with its own latency budget. Numbers are target budgets, validated in loadtest/.")
q = [
    ("q", "POST /v1/query (stream)", "start", 40, 100),
    ("mem", "Load conversation buffer +<br>rolling summary + user facts", "step", 40, 190),
    ("route", "Query router (fast LLM, structured)<br>chit-chat | lookup | multi-hop | global", "decision", 40, 290),
    ("rw", "Condense to standalone query<br>(skip on first short turn)", "step", 40, 420),
    ("par", "Parallel: dense ANN ‖ BM25 sparse<br>(tenant + ACL filter in-query)", "step", 330, 420),
    ("rrf", "RRF fusion (k=60) /<br>weighted per collection", "step", 620, 420),
    ("thr", "Top margin high?", "decision", 900, 405),
    ("rr", "Cross-encoder rerank<br>top 50 → top k", "step", 900, 540),
    ("ctx", "Context assembly: dedupe, merge adjacent,<br>small-to-big, token budget", "step", 620, 650),
    ("abst", "Evidence ≥ threshold?", "decision", 330, 635),
    ("idk", "Abstain: 'not in knowledge base'", "end", 40, 650),
    ("llm", "Gateway → LLM (stream)<br>cached system prefix", "step", 330, 780),
    ("ver", "Citation verifier (async):<br>quote match + entailment", "step", 620, 780),
    ("save", "Persist turn · extract memories (async)<br>emit spans · usage", "step", 900, 780),
    ("done", "SSE: tokens → citations → verification", "end", 1180, 790),
]
for k, lbl, kind, x, yy in q:
    w, h = (230, 80) if kind == "decision" else (230, 55)
    p.node(k, lbl, x, yy, w, h, kind=kind)
for a, b, l in [("q", "mem", ""), ("mem", "route", ""), ("route", "rw", "needs retrieval"), ("rw", "par", ""),
                ("par", "rrf", ""), ("rrf", "thr", ""), ("thr", "rr", "no"), ("thr", "ctx", "yes (skip rerank)"),
                ("rr", "ctx", ""), ("ctx", "abst", ""), ("abst", "idk", "no"), ("abst", "llm", "yes"),
                ("llm", "ver", ""), ("ver", "save", ""), ("save", "done", "")]:
    p.edge(a, b, l, kind="flow")
p.node("cc", "chit-chat → direct cheap LLM, no retrieval", 330, 300, 250, 50, kind="note")
p.edge("route", "cc", "chit-chat", kind="flow")
p.node("bud", "<b>Latency budget (p95)</b><br>memory load 10 ms · router 250 ms · rewrite 250 ms<br>retrieval 80 ms · rerank 120 ms · "
       "TTFT 800 ms", 1180, 100, 300, 90, kind="note")

# ---------------------------------------------------------------------------
# 5. Memory architecture
# ---------------------------------------------------------------------------
p = doc.page("5. Memory")
p.title("Memory: three tiers with provenance",
        "ADD-only extraction (cheap, no destructive writes in the hot path) + background consolidation that supersedes stale facts bi-temporally.")
t1 = p.group("t1", "Tier 1: Working memory (per conversation)", 30, 90, 460, 230, kind="zone")
p.node("buf", "Token-bounded message buffer<br>(last N turns ≤ budget)", 20, 40, 200, 60, kind="component", parent=t1)
p.node("sum", "Rolling summary<br>(regenerated when buffer overflows)", 240, 40, 200, 60, kind="component", parent=t1)
p.node("cond", "History-aware query condensation<br>→ standalone retrieval query", 20, 130, 420, 60, kind="component", parent=t1)
t2 = p.group("t2", "Tier 2: Long-term user memory (facts & preferences)", 530, 90, 520, 230, kind="zone")
p.node("ext", "Extractor (fast LLM, structured)<br>ADD-only candidate facts", 20, 40, 220, 60, kind="worker", parent=t2)
p.node("store", "memory_facts (Postgres)<br>+ embeddings (Qdrant)<br>valid_from / invalid_at / source_msg", 260, 35, 240, 75, kind="db", parent=t2)
p.node("cons", "Consolidator (cron / threshold)<br>merge duplicates · supersede contradictions", 20, 140, 480, 60, kind="worker", parent=t2)
t3 = p.group("t3", "Tier 3: Collection memory (shared)", 1090, 90, 380, 230, kind="zone")
p.node("faq", "Answer cache + feedback-corrected<br>answers (promoted to eval set)", 20, 40, 340, 60, kind="component", parent=t3)
p.node("dsum", "Document summaries<br>(summary index for global Qs)", 20, 130, 340, 60, kind="component", parent=t3)
p.node("turn", "Each turn", 230, 400, kind="start")
p.node("prompt", "Prompt assembly:<br>pinned user facts (top-k relevant) + summary + buffer + evidence", 500, 390, 420, 60, kind="step")
p.edge("turn", "buf", kind="flow")
p.edge("buf", "sum", "overflow", kind="async")
p.edge("turn", "ext", "async", kind="async")
p.edge("ext", "store", kind="data")
p.edge("cons", "store", kind="data")
p.edge("store", "prompt", "retrieve relevant facts")
p.edge("cond", "prompt")
p.node("gdpr", "<b>Controls</b><br>GET/DELETE /v1/memory (user can see & erase)<br>per-tenant retention · PII redaction · provenance links to source message",
       30, 490, 460, 80, kind="note")

# ---------------------------------------------------------------------------
# 6. Deep research agent graph
# ---------------------------------------------------------------------------
p = doc.page("6. Deep Research Agents")
p.title("Deep Research: Planner → Researchers ‖ → Writer → Reviewer",
        "LangGraph state machine, Postgres checkpointer (resumable), budget guard checked at every node, effort levels low/medium/high.")
p.node("s", "research job", 40, 200, kind="start")
p.node("plan", "Planner<br>(standard tier)<br>sub-questions + outline", 220, 180, kind="agent", w=180, h=90)
p.node("r1", "Researcher 1 (fast)", 480, 100, kind="agent")
p.node("r2", "Researcher 2 (fast)", 480, 200, kind="agent")
p.node("r3", "Researcher N (fast)", 480, 300, kind="agent")
p.node("tools", "Tools: kb_search · web_search (opt) · fetch", 470, 400, 200, 50, kind="note")
p.node("write", "Writer<br>(standard/deep)<br>cites chunk ids only", 740, 180, kind="agent", w=180, h=90)
p.node("rev", "Reviewer<br>coverage · support · contradictions", 990, 180, kind="agent", w=190, h=90)
p.node("dec", "approved?<br>loops < max", 1250, 185, kind="decision")
p.node("fin", "Report + citations", 1260, 330, kind="end")
p.node("budg", "Budget guard (tokens · $ · wall time) → graceful partial report", 220, 330, 230, 60, kind="note")
for a, b in [("s", "plan"), ("plan", "r1"), ("plan", "r2"), ("plan", "r3"), ("r1", "write"), ("r2", "write"),
             ("r3", "write"), ("write", "rev"), ("rev", "dec")]:
    p.edge(a, b, kind="flow")
p.edge("dec", "fin", "yes", kind="flow")
p.edge("dec", "plan", "gaps → re-plan", kind="async")

# ---------------------------------------------------------------------------
# 7. Data model
# ---------------------------------------------------------------------------
p = doc.page("7. Data Model")
p.title("Postgres data model (tenant-scoped tables use FORCE ROW LEVEL SECURITY)")
p.entity("tenants", "tenants", ["PK id uuid", "name", "plan", "monthly_budget_usd"], 30, 90)
p.entity("keys", "api_keys", ["PK id uuid", "FK tenant_id", "key_hash (sha256)", "scopes text[]", "rpm / tpm", "budget_usd_day", "revoked_at"], 30, 260)
p.entity("users", "users", ["PK id uuid", "FK tenant_id", "external_id", "groups text[]"], 30, 480)
p.entity("col", "collections", ["PK id uuid", "FK tenant_id", "name", "embedding_model", "chunking jsonb", "retrieval jsonb", "qdrant_alias"], 320, 90)
p.entity("docs", "documents", ["PK id uuid", "FK tenant_id", "FK collection_id", "source_uri", "mime", "content_hash", "version", "state", "acl_groups text[]"], 320, 300)
p.entity("stages", "document_stages", ["PK (doc_id, version, stage)", "status", "attempts", "error", "started_at / finished_at"], 320, 560)
p.entity("chunks", "chunks", ["PK id uuid", "FK tenant_id, doc_id", "version, ordinal", "parent_id", "section_path", "page_start/page_end", "bboxes jsonb", "text_hash", "context_text"], 610, 90)
p.entity("conv", "conversations", ["PK id uuid", "FK tenant_id, user_id", "summary", "summary_upto_msg"], 610, 380)
p.entity("msgs", "messages", ["PK id uuid", "FK conversation_id", "role", "content", "citations jsonb", "token_count"], 610, 540)
p.entity("facts", "memory_facts", ["PK id uuid", "FK tenant_id, user_id", "fact", "category", "source_message_id", "valid_from", "invalid_at", "superseded_by"], 900, 90)
p.entity("calls", "llm_calls", ["PK id", "tenant_id, key_id, route", "provider, model", "in/out/cache tokens", "cost_usd", "latency_ms, ttft_ms", "cache_hit, status", "trace_id, ts"], 900, 330)
p.entity("jobs", "research_jobs", ["PK id uuid", "FK tenant_id", "question, effort", "status", "budget / spent jsonb", "report jsonb"], 1190, 90)
p.entity("fb", "feedback", ["PK id uuid", "FK message_id", "rating", "comment", "corrected_answer"], 1190, 300)
p.entity("evals", "eval_runs", ["PK id uuid", "git_sha", "dataset_version", "config jsonb", "metrics jsonb", "ts"], 1190, 470)
for a, b in [("tenants", "keys"), ("tenants", "users"), ("tenants", "col"), ("col", "docs"), ("docs", "stages"),
             ("docs", "chunks"), ("users", "conv"), ("conv", "msgs"), ("users", "facts"), ("msgs", "fb")]:
    p.edge(a, b, kind="relation")

# ---------------------------------------------------------------------------
# 8. Evaluation & CI
# ---------------------------------------------------------------------------
p = doc.page("8. Evaluation & CI")
p.title("Evaluation loop: offline gate in CI + online feedback → dataset",
        "LLM-judged evals run only when retrieval/prompt/ingest paths change (path-filtered, cost-bounded).")
p.node("pr", "Pull request", 40, 120, kind="start")
p.node("lint", "ruff · mypy · unit tests", 220, 120, kind="step")
p.node("pf", "paths changed:<br>retrieval/ generation/ ingestion/ config/?", 430, 105, 240, 80, kind="decision")
p.node("stack", "docker compose -f compose.yaml<br>-f compose.ci.yaml up (mock-llm)", 720, 120, 230, 55, kind="step")
p.node("ing", "ingest eval corpus", 990, 120, kind="step")
p.node("run", "run eval suite<br>retrieval: recall@k, nDCG, MRR<br>generation: faithfulness, citation P/R, abstention", 720, 240, 330, 70, kind="step")
p.node("cmp", "compare to baselines/*.json<br>(fail if faithfulness −2 pts or recall@10 −3 pts)", 1090, 240, 280, 60, kind="decision")
p.node("cmt", "PR comment: metrics diff table", 1090, 380, 230, 50, kind="end")
p.node("img", "build & scan images (Trivy) · SBOM", 430, 380, 230, 50, kind="step")
p.node("prod", "Production traffic", 40, 520, kind="start")
p.node("fbk", "👍/👎 + corrections + low verifier scores", 260, 515, 250, 55, kind="step")
p.node("triage", "triage queue → human-labelled", 560, 515, 220, 55, kind="step")
p.node("ds", "evals/datasets (versioned)", 840, 515, 200, 60, kind="db")
for a, b, l in [("pr", "lint", ""), ("lint", "pf", ""), ("pf", "stack", "yes"), ("stack", "ing", ""), ("ing", "run", ""),
                ("run", "cmp", ""), ("cmp", "cmt", ""), ("pf", "img", "no"), ("prod", "fbk", ""), ("fbk", "triage", ""),
                ("triage", "ds", "")]:
    p.edge(a, b, l, kind="flow")
p.edge("ds", "run", "next run", kind="async")

# ---------------------------------------------------------------------------
# 9. Deployment
# ---------------------------------------------------------------------------
p = doc.page("9. Deployment")
p.title("Deployment: one compose file, opt-in profiles; Helm for Kubernetes",
        "docker compose --profile <name> up. 'migrate' is a one-shot service; app services wait for it via depends_on: service_completed_successfully.")
prof = [("default", "postgres · qdrant · redis · minio · migrate · tei-embed · tei-rerank · gateway · api · worker-ingest · worker-light · mcp", 90),
        ("mock", "mock-llm (deterministic provider for CI, chaos tests, offline demos)", 200),
        ("ollama", "ollama + model pull (fully local LLM, no API keys)", 290),
        ("docling", "worker-ingest built with Docling extras (layout-aware PDF, tables, OCR)", 380),
        ("gpu", "TEI GPU images + ollama with nvidia device reservation", 470),
        ("observability", "otel-collector · jaeger · prometheus · grafana (or point OTLP at Lumen)", 560)]
for name, desc, yy in prof:
    p.node(f"pf-{name}", f"profile: <b>{name}</b>", 40, yy, 200, 60, kind="service")
    p.node(f"pd-{name}", desc, 270, yy, 700, 60, kind="note")
p.node("k8s", "<b>deploy/helm/atlas</b><br>Deployments: api, gateway, workers (HPA on queue depth)<br>StatefulSets or managed: Postgres, Qdrant, Redis, S3<br>"
       "Job: migrate (pre-upgrade hook)", 1010, 90, 420, 120, kind="note")

# ---------------------------------------------------------------------------
# 10. Module map
# ---------------------------------------------------------------------------
p = doc.page("10. Code Module Map")
p.title("src/atlas: module dependencies (arrows = 'imports')",
        "Rule: api/ and workers/ are thin adapters; domain packages never import from api/. common/ imports nothing internal.")
mods = [("api", "api/ (routers, deps, schemas)", 40, 100), ("workers", "workers/ (arq tasks)", 40, 220),
        ("mcp", "mcp_server/", 40, 340), ("gwapp", "gateway/ (separate app)", 40, 460),
        ("kb", "kb/ (collections, documents, chunks)", 380, 100), ("ing", "ingestion/", 380, 220),
        ("retr", "retrieval/", 380, 340), ("gen", "generation/", 380, 460), ("mem", "memory/", 380, 580),
        ("res", "research/", 380, 700),
        ("idx", "index/ (Qdrant)", 760, 220), ("emb", "embeddings/ (TEI client + cache)", 760, 340),
        ("llm", "llm/ (gateway client)", 760, 460), ("db", "db/ (models, session, RLS)", 760, 580),
        ("common", "common/ (settings, logging, telemetry, errors)", 1100, 400)]
for k, lbl, x, yy in mods:
    p.node(k, lbl, x, yy, 260, 50, kind="component")
for a, b in [("api", "kb"), ("api", "retr"), ("api", "gen"), ("api", "mem"), ("api", "res"), ("workers", "ing"),
             ("workers", "mem"), ("mcp", "api"), ("ing", "idx"), ("ing", "emb"), ("ing", "llm"), ("retr", "idx"),
             ("retr", "emb"), ("retr", "llm"), ("gen", "llm"), ("mem", "llm"), ("mem", "db"), ("res", "retr"),
             ("res", "llm"), ("kb", "db"), ("idx", "common"), ("emb", "common"), ("llm", "common"), ("db", "common"),
             ("gwapp", "common")]:
    p.edge(a, b, kind="sync")

out = doc.save(Path(__file__).with_name("atlas.drawio"))
print(f"wrote {out}")
