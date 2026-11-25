"""Endpoints that power the web console: demo session, system info, research list.

``POST /v1/demo/session`` exists ONLY when ATLAS_DEMO_MODE=true (local/dev). It idempotently
creates a demo tenant, an admin key and a sample collection seeded with the bundled corpus, so
the whole platform can be explored with one click after ``docker compose up``.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Request
from sqlalchemy import select

from atlas.api.security import ReadCtx
from atlas.common.errors import NotFound
from atlas.common.settings import get_settings
from atlas.db.apikeys import create_key, lookup_key
from atlas.db.models import Collection, ResearchJob, Tenant
from atlas.db.session import admin_session, tenant_session
from atlas.kb.context import RequestContext

router = APIRouter(prefix="/v1", tags=["console"])
DEMO_TENANT = "demo"
CORPUS = Path(os.environ.get("ATLAS_DEMO_CORPUS", "evals/corpus"))


@router.post("/demo/session")
async def demo_session(request: Request) -> dict[str, Any]:
    s = get_settings()
    if not s.demo_mode:
        raise NotFound("demo mode is disabled")
    redis = request.app.state.c.redis
    lock = redis.lock("demo:bootstrap", timeout=60)
    async with lock:
        raw = await redis.get("demo:api_key")
        key = raw.decode() if raw else None
        if key is None or await lookup_key(redis, key) is None:
            async with admin_session() as sess:
                tenant = (await sess.execute(select(Tenant).where(Tenant.name == DEMO_TENANT))).scalar_one_or_none()
                if tenant is None:
                    tenant = Tenant(id=uuid.uuid4(), name=DEMO_TENANT, monthly_budget_usd=25.0)
                    sess.add(tenant)
            key, _ = await create_key(tenant.id, "demo-console", ["admin", "read", "write", "llm", "impersonate"],
                                      budget_usd_day=5.0)
            await redis.set("demo:api_key", key)
        info = await lookup_key(redis, key)
        assert info is not None
        ctx = RequestContext(tenant_id=info.tenant_id, key_id=info.key_id, scopes=info.scopes)
        kb = request.app.state.kb
        async with tenant_session(info.tenant_id) as sess:
            coll = (await sess.execute(select(Collection).where(Collection.name == "Kestrel handbook"))
                    ).scalar_one_or_none()
        seeded = 0
        if coll is None:
            coll = await kb.create_collection(ctx, "Kestrel handbook",
                                              "Sample company documents (policies, product manual, quarterly report)")
            for f in sorted(CORPUS.glob("*.md")):
                await kb.add_document(ctx, coll.id, data=f.read_bytes(), filename=f.name,
                                      declared_mime="text/markdown", title=f.stem.replace("_", " ").title())
                seeded += 1
    return {"api_key": key, "tenant_id": info.tenant_id, "collection_id": str(coll.id), "seeded_documents": seeded,
            "pulse": {"url": s.pulse_public_url, "key": s.otlp_api_key}, "llm_live": _llm_live()}


def _llm_live() -> bool:
    return os.environ.get("ATLAS_LLM_LIVE", "false").lower() == "true"


@router.get("/system/info")
async def system_info(request: Request, ctx: ReadCtx) -> dict[str, Any]:
    s = get_settings()
    routes: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{s.gateway_url}/v1/models",
                                 headers={"Authorization": f"Bearer {s.gateway_internal_key.get_secret_value()}"})
            routes = r.json().get("data", [])
    except httpx.HTTPError:
        pass
    return {"env": s.env, "llm_live": _llm_live(), "demo_mode": s.demo_mode, "embed_model": s.embed_model,
            "embed_dim": s.embed_dim, "rerank_enabled": s.rerank_enabled, "parser": s.parser,
            "contextual_enrichment": s.contextual_enrichment, "capture_content": s.capture_content,
            "pipeline_version": s.pipeline_version, "gateway_routes": routes,
            "retrieval_defaults": {"candidates": s.retrieval_candidates, "top_k": s.retrieval_top_k,
                                   "context_token_budget": s.context_token_budget,
                                   "abstain_threshold": s.abstain_threshold},
            "pulse": {"url": s.pulse_public_url}}


@router.get("/research")
async def list_research(ctx: ReadCtx, limit: int = 50) -> list[dict[str, Any]]:
    async with tenant_session(ctx.tenant_id) as sess:
        rows = (await sess.execute(select(ResearchJob).order_by(ResearchJob.created_at.desc())
                                   .limit(min(limit, 200)))).scalars()
        return [{"job_id": str(j.id), "question": j.question, "effort": j.effort, "status": j.status,
                 "spent": j.spent, "created_at": j.created_at.isoformat() if j.created_at else None} for j in rows]
