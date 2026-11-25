"""Queue topology (split by workload, Onyx-style): one slow ingest doc can't starve memory jobs or
research, and each queue scales independently (HPA on queue depth)."""

INGEST = "atlas:ingest"      # CPU/IO heavy: parse, chunk, embed, index
LIGHT = "atlas:light"        # cheap LLM jobs: enrichment, memory extraction, summaries
RESEARCH = "atlas:research"  # long-running multi-agent research

QUEUE_FOR = {
    "ingest_document": INGEST,
    "enrich_document": LIGHT,
    "extract_memories": LIGHT,
    "roll_summary": LIGHT,
    "consolidate_memories": LIGHT,
    "run_research": RESEARCH,
}
