"""User memory (view / erase), deep research jobs (+ live SSE progress), and usage reports."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from typing import Any

import orjson
from fastapi import APIRouter, Request
from sqlalchemy import func, select, text
from sse_starlette.sse import EventSourceResponse

from atlas.api.schemas import ResearchRequest
from atlas.api.security import Ctx, ReadCtx
from atlas.common.errors import InvalidInput, NotFound
from atlas.db.models import Collection, MemoryFact, ResearchJob
from atlas.db.session import tenant_session
from atlas.memory.facts import FactStore
from atlas.research.graph import EFFORT
from atlas.research.runner import events_key
from atlas.workers.queues import RESEARCH

router = APIRouter(prefix="/v1", tags=["memory", "research", "usage"])


# ---------------------------------------------------------------------------- memory
@router.get("/memory")
async def list_memory(ctx: Ctx, include_invalid: bool = False) -> list[dict[str, Any]]:
    if ctx.user_id is None:
        raise InvalidInput("memory requires a user (user-bound key or X-Atlas-User)")
    async with tenant_session(ctx.tenant_id) as s:
        q = select(MemoryFact).where(MemoryFact.user_id == ctx.user_id).order_by(MemoryFact.created_at.desc())
        if not include_invalid:
            q = q.where(MemoryFact.invalid_at.is_(None))
        return [{"id": str(f.id), "fact": f.fact, "category": f.category, "confidence": f.confidence,
                 "valid_from": f.valid_from.isoformat(), "invalid_at": f.invalid_at.isoformat() if f.invalid_at else None,
                 "superseded_by": str(f.superseded_by) if f.superseded_by else None,
                 "source_message_id": str(f.source_message_id) if f.source_message_id else None}
                for f in (await s.execute(q)).scalars()]


@router.delete("/memory")
async def forget_all(ctx: Ctx, request: Request) -> dict[str, int]:
    return await _forget(ctx, request, None)


@router.delete("/memory/{fact_id}")
async def forget_one(fact_id: uuid.UUID, ctx: Ctx, request: Request) -> dict[str, int]:
    return await _forget(ctx, request, fact_id)


async def _forget(ctx: Any, request: Request, fact_id: uuid.UUID | None) -> dict[str, int]:
    if ctx.user_id is None:
        raise InvalidInput("memory requires a user")
    store: FactStore = request.app.state.facts
    async with tenant_session(ctx.tenant_id) as s:
        n = await store.delete_for_user(s, ctx.user_id, fact_id)
    return {"deleted": n}


# ---------------------------------------------------------------------------- research
@router.post("/research", status_code=202)
async def start_research(body: ResearchRequest, ctx: ReadCtx, request: Request) -> dict[str, Any]:
    async with tenant_session(ctx.tenant_id) as s:
        found = (await s.execute(select(func.count()).select_from(Collection)
                                 .where(Collection.id.in_(body.collection_ids)))).scalar_one()
        if found != len(set(body.collection_ids)):
            raise NotFound("collection not found")
        job = ResearchJob(id=uuid.uuid4(), tenant_id=uuid.UUID(ctx.tenant_id), user_id=ctx.user_id,
                          question=body.question, effort=body.effort,
                          collection_ids=[str(c) for c in body.collection_ids],
                          budget={"usd": EFFORT[body.effort].budget_usd})
        s.add(job)
    await request.app.state.arq.enqueue_job("run_research", ctx.tenant_id, str(job.id), _queue_name=RESEARCH,
                                            _job_id=f"research:{job.id}")
    return {"job_id": str(job.id), "status": "queued", "budget_usd": EFFORT[body.effort].budget_usd}


@router.get("/research/{job_id}")
async def get_research(job_id: uuid.UUID, ctx: ReadCtx) -> dict[str, Any]:
    async with tenant_session(ctx.tenant_id) as s:
        job = await s.get(ResearchJob, job_id)
    if job is None:
        raise NotFound("job not found")
    return {"job_id": str(job.id), "status": job.status, "question": job.question, "effort": job.effort,
            "report": job.report, "spent": job.spent, "error": job.error}


@router.get("/research/{job_id}/events")
async def research_events(job_id: uuid.UUID, ctx: ReadCtx, request: Request) -> EventSourceResponse:
    async with tenant_session(ctx.tenant_id) as s:
        if await s.get(ResearchJob, job_id) is None:  # RLS: other tenants' jobs are invisible
            raise NotFound("job not found")
    redis = request.app.state.c.redis
    key = events_key(str(job_id))

    async def stream() -> AsyncIterator[dict[str, str]]:
        pubsub = redis.pubsub()
        await pubsub.subscribe(key)
        try:
            for raw in await redis.lrange(key, 0, -1):  # replay history first
                ev = orjson.loads(raw)
                yield {"event": ev["type"], "data": raw.decode()}
                if ev["type"] in ("done", "error"):
                    return
            while not await request.is_disconnected():
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=5.0)
                if msg is None:
                    await asyncio.sleep(0.1)
                    continue
                ev = orjson.loads(msg["data"])
                yield {"event": ev["type"], "data": msg["data"].decode()}
                if ev["type"] in ("done", "error"):
                    return
        finally:
            await pubsub.unsubscribe(key)
            await pubsub.aclose()

    return EventSourceResponse(stream(), ping=15)


# ---------------------------------------------------------------------------- usage
@router.get("/usage")
async def usage(ctx: ReadCtx, days: int = 7) -> dict[str, Any]:
    """Spend, tokens and cache savings by route/model (from the async usage ledger)."""
    async with tenant_session(ctx.tenant_id) as s:
        rows = (await s.execute(text("""
            SELECT date_trunc('day', ts) AS day, route, coalesce(model, '-') AS model, count(*) AS calls,
                   sum(prompt_tokens) AS input, sum(completion_tokens) AS output,
                   sum(cache_read_tokens) AS cache_read, sum(cost_usd) AS cost,
                   sum(uncached_cost_usd) - sum(cost_usd) AS saved,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95_ms,
                   avg(CASE WHEN cache <> 'miss' THEN 1 ELSE 0 END) AS cache_hit_rate
            FROM llm_calls WHERE ts > now() - make_interval(days => :days)
            GROUP BY 1, 2, 3 ORDER BY 1 DESC, cost DESC"""), {"days": min(days, 90)})).mappings().all()
    items = [{k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in rows]
    return {"days": days, "total_cost_usd": round(sum(float(r["cost"] or 0) for r in rows), 6),
            "total_saved_usd": round(sum(float(r["saved"] or 0) for r in rows), 6), "rows": items}
