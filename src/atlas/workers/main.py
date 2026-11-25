"""arq worker entrypoints.  Run one per queue:

    arq atlas.workers.main.IngestWorker
    arq atlas.workers.main.LightWorker
    arq atlas.workers.main.ResearchWorker

Retry policy: transient failures (upstream/model/network) are retried with linear backoff up to
``max_tries``; permanent failures (bad input) are recorded on the document and not retried.
Job ids double as idempotency keys (``ingest:<doc>:<version>``), so duplicate enqueues are no-ops.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from arq import Retry, cron
from arq.connections import RedisSettings
from sqlalchemy import select

from atlas.common.errors import InvalidInput, NotFound, UpstreamError
from atlas.common.logging import configure_logging, get_logger
from atlas.common.settings import get_settings
from atlas.common.telemetry import observe, setup_telemetry, trace_context
from atlas.container import Container
from atlas.db.models import Document, MemoryFact, Message
from atlas.db.session import admin_session, tenant_session
from atlas.ingestion.pipeline import enrich_document, ingest_document
from atlas.memory.conversation import roll_summary as _roll_summary
from atlas.memory.facts import FactStore
from atlas.research.runner import run_research as _run_research
from atlas.workers.queues import INGEST, LIGHT, QUEUE_FOR, RESEARCH

log = get_logger(__name__)
TRANSIENT = (UpstreamError, ConnectionError, TimeoutError)


async def startup(ctx: dict[str, Any]) -> None:
    s = get_settings()
    configure_logging(s.log_level)
    setup_telemetry(f"{s.service_name}-worker", s.otlp_endpoint, api_key=s.otlp_api_key, capture_content=s.capture_content, environment=s.env)
    c = Container.build(s)
    await c.objects.ensure_bucket()
    ctx["c"] = c
    ctx["facts"] = FactStore(c.qdrant, c.embedder, c.llm)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["c"].aclose()


async def _enqueue(ctx: dict[str, Any], fn: str, *args: Any, **kw: Any) -> None:
    kw.setdefault("_queue_name", QUEUE_FOR[fn])
    await ctx["redis"].enqueue_job(fn, *args, **kw)


def _retry(ctx: dict[str, Any], exc: Exception) -> Retry:
    log.warning("job_retry", job_try=ctx.get("job_try"), error=str(exc)[:300])
    return Retry(defer=timedelta(seconds=15 * ctx.get("job_try", 1)))


# ---------------------------------------------------------------------------- tasks
async def ingest_document_task(ctx: dict[str, Any], tenant_id: str, document_id: str) -> dict[str, Any]:
    with trace_context(name="ingest", tags=["atlas", "ingest"], session_id=f"doc:{document_id}",
                       metadata={"tenant_id": tenant_id, "job_try": ctx.get("job_try")}),             observe("ingest", type="span", input={"document_id": document_id}) as obs:
        try:
            stats = await ingest_document(ctx["c"], tenant_id, document_id)
        except (InvalidInput, NotFound) as exc:
            log.warning("ingest_permanent_failure", document_id=document_id, error=str(exc))
            obs.error(f"permanent failure: {exc}")
            return {"error": str(exc)}
        except TRANSIENT as exc:
            obs.warn(f"transient failure, will retry: {exc}")
            raise _retry(ctx, exc) from exc
        obs.set_output(stats)
    if ctx["c"].settings.contextual_enrichment:
        await _enqueue(ctx, "enrich_document", tenant_id, document_id)
    return stats


async def enrich_document_task(ctx: dict[str, Any], tenant_id: str, document_id: str) -> dict[str, Any]:
    with trace_context(name="enrich", tags=["atlas", "ingest"], session_id=f"doc:{document_id}",
                       metadata={"tenant_id": tenant_id}),             observe("enrich", type="span", input={"document_id": document_id}) as obs:
        try:
            out = await enrich_document(ctx["c"], tenant_id, document_id)
        except TRANSIENT as exc:
            obs.warn(f"transient failure, will retry: {exc}")
            raise _retry(ctx, exc) from exc
        obs.set_output(out)
        return out


async def extract_memories_task(ctx: dict[str, Any], tenant_id: str, user_id: str, message_id: str) -> int:
    async with tenant_session(tenant_id) as s:
        msg = await s.get(Message, uuid.UUID(message_id))
        if msg is None:
            return 0
        with trace_context(name="memory.extract", tags=["atlas", "memory"], user_id=user_id,
                           metadata={"tenant_id": tenant_id}),                 observe("memory.extract", type="span", input=msg.content) as obs:
            n = await ctx["facts"].extract_and_add(s, uuid.UUID(tenant_id), uuid.UUID(user_id), msg.content, msg.id)
            obs.set_output({"facts_added": n})
            return int(n)


async def roll_summary_task(ctx: dict[str, Any], tenant_id: str, conversation_id: str) -> bool:
    async with tenant_session(tenant_id) as s:
        return await _roll_summary(s, ctx["c"].llm, uuid.UUID(conversation_id), ctx["c"].settings.memory_buffer_tokens)


async def consolidate_memories_task(ctx: dict[str, Any], tenant_id: str, user_id: str) -> dict[str, int]:
    async with tenant_session(tenant_id) as s:
        return await ctx["facts"].consolidate(s, uuid.UUID(tenant_id), uuid.UUID(user_id))  # type: ignore[no-any-return]


async def run_research_task(ctx: dict[str, Any], tenant_id: str, job_id: str) -> dict[str, Any]:
    with trace_context(name="research", tags=["atlas", "research"], session_id=f"research:{job_id}",
                       metadata={"tenant_id": tenant_id}),             observe("research", type="agent", input={"job_id": job_id}) as obs:
        out = await _run_research(ctx["c"], tenant_id, job_id)
        obs.set_output(out)
        return out


# ---------------------------------------------------------------------------- crons
async def nightly_consolidation(ctx: dict[str, Any]) -> int:
    """Consolidate memory for users who gained facts in the last 24h ('sleep-time' compute)."""
    since = datetime.now(UTC) - timedelta(days=1)
    async with admin_session() as s:
        pairs = (await s.execute(select(MemoryFact.tenant_id, MemoryFact.user_id).where(
            MemoryFact.created_at >= since).distinct())).all()
    for tenant_id, user_id in pairs:
        await _enqueue(ctx, "consolidate_memories", str(tenant_id), str(user_id))
    return len(pairs)


async def sweep_stuck_documents(ctx: dict[str, Any]) -> int:
    """Re-enqueue documents stuck in a non-terminal state (worker crash, lost job). A known
    failure mode in other RAG systems ('stuck at queuing'); here it self-heals."""
    cutoff = datetime.now(UTC) - timedelta(minutes=30)
    async with admin_session() as s:
        rows = (await s.execute(select(Document.tenant_id, Document.id, Document.version).where(
            Document.state.notin_(["ready", "failed"]), Document.updated_at < cutoff).limit(200))).all()
    for tenant_id, doc_id, version in rows:
        await _enqueue(ctx, "ingest_document", str(tenant_id), str(doc_id),
                       _job_id=f"ingest:{doc_id}:{version}:sweep:{datetime.now(UTC):%Y%m%d%H}")
    if rows:
        log.warning("stuck_documents_requeued", count=len(rows))
    return len(rows)


def _redis() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


def _fn(f: Any, name: str, timeout: int) -> Any:
    from arq.worker import func

    return func(f, name=name, timeout=timeout)


def _worker(name: str, queue: str, functions: list[Any], max_jobs: int, **extra: Any) -> type:
    """arq reads WorkerSettings via ``cls.__dict__`` (inherited attributes are ignored!), so every worker
    class is built flat with the full set of settings."""
    attrs: dict[str, Any] = {
        "on_startup": startup, "on_shutdown": shutdown, "redis_settings": _redis(), "max_tries": 4,
        "health_check_interval": 30, "keep_result": 3600, "queue_name": queue, "functions": functions,
        "max_jobs": max_jobs, **extra}
    return type(name, (), attrs)


IngestWorker = _worker("IngestWorker", INGEST, [_fn(ingest_document_task, "ingest_document", 900)],
                       get_settings().ingest_concurrency)
LightWorker = _worker("LightWorker", LIGHT, [
    _fn(enrich_document_task, "enrich_document", 1800), _fn(extract_memories_task, "extract_memories", 120),
    _fn(roll_summary_task, "roll_summary", 120), _fn(consolidate_memories_task, "consolidate_memories", 600)],
    16, cron_jobs=[cron(nightly_consolidation, hour=3, minute=0),
                   cron(sweep_stuck_documents, minute={0, 15, 30, 45})])
ResearchWorker = _worker("ResearchWorker", RESEARCH, [_fn(run_research_task, "run_research", 1800)], 4)
