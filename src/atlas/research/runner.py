"""Runs a research job on the `research` queue with durable checkpoints and live progress events.

Progress events go to a Redis list (replayable for late subscribers) + pub/sub (live SSE).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import orjson
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from sqlalchemy import select, update

from atlas.common.logging import get_logger
from atlas.container import Container
from atlas.db.models import Collection, ResearchJob
from atlas.db.session import tenant_session
from atlas.research.graph import EFFORT, build_graph
from atlas.retrieval.service import RetrievalService

log = get_logger(__name__)


def events_key(job_id: str) -> str:
    return f"research:events:{job_id}"


def _pg_url(sqlalchemy_url: str) -> str:
    return sqlalchemy_url.replace("postgresql+asyncpg://", "postgresql://")


async def run_research(c: Container, tenant_id: str, job_id: str) -> dict[str, Any]:
    async def publish(jid: str, event: dict[str, Any]) -> None:
        raw = orjson.dumps(event)
        await c.redis.rpush(events_key(jid), raw)
        await c.redis.expire(events_key(jid), 86400)
        await c.redis.publish(events_key(jid), raw)

    async with tenant_session(tenant_id) as s:
        job = await s.get(ResearchJob, uuid.UUID(job_id))
        if job is None:
            return {"error": "job not found"}
        colls = {str(x.id): x for x in (await s.execute(select(Collection).where(
            Collection.id.in_([uuid.UUID(i) for i in job.collection_ids])))).scalars()}
        await s.execute(update(ResearchJob).where(ResearchJob.id == job.id).values(status="running"))

    retrieval = RetrievalService(c)
    graph = build_graph(c, retrieval, colls, publish)
    config = {"configurable": {"thread_id": job_id}, "recursion_limit": 60}
    try:
        async with AsyncPostgresSaver.from_conn_string(_pg_url(c.settings.database_admin_url)) as saver:
            await saver.setup()  # idempotent
            app = graph.compile(checkpointer=saver)
            existing = await saver.aget_tuple(config)  # type: ignore[arg-type]
            inputs = None if existing else {
                "job_id": job_id, "tenant_id": tenant_id, "question": job.question, "effort": job.effort,
                "collection_ids": list(job.collection_ids), "groups": [], "evidence": [], "spent_usd": 0.0,
                "loops": 0}
            if existing:
                log.info("research_resume", job_id=job_id)
            final = await app.ainvoke(inputs, config=config)  # type: ignore[arg-type]
        report = {"markdown": final.get("report", ""), "outline": final.get("outline", []),
                  "references": final.get("review", {}).get("references", {}),
                  "review": {k: v for k, v in final.get("review", {}).items() if k != "references"},
                  "partial": final.get("partial", False)}
        spent = {"usd": round(final.get("spent_usd", 0.0), 6), "budget_usd": EFFORT[job.effort].budget_usd,
                 "evidence": len(final.get("evidence", [])), "loops": final.get("loops", 0)}
        async with tenant_session(tenant_id) as s:
            await s.execute(update(ResearchJob).where(ResearchJob.id == uuid.UUID(job_id)).values(
                status="succeeded", report=report, spent=spent, finished_at=datetime.now(UTC)))
        await publish(job_id, {"type": "done", "spent": spent})
        return spent
    except Exception as exc:
        log.exception("research_failed", job_id=job_id)
        async with tenant_session(tenant_id) as s:
            await s.execute(update(ResearchJob).where(ResearchJob.id == uuid.UUID(job_id)).values(
                status="failed", error=str(exc)[:2000], finished_at=datetime.now(UTC)))
        await publish(job_id, {"type": "error", "message": str(exc)[:500]})
        raise
    finally:
        await retrieval.aclose()
