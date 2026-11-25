"""Search (hit testing), query (cited answers, SSE or JSON), conversations and feedback."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import orjson
from fastapi import APIRouter, Request
from sqlalchemy import select
from sse_starlette.sse import EventSourceResponse

from atlas.api.schemas import FeedbackRequest, QueryRequest, SearchRequest
from atlas.api.security import Ctx, ReadCtx
from atlas.common.errors import AtlasError, NotFound
from atlas.db.models import Collection, Conversation, Feedback, Message
from atlas.db.session import tenant_session
from atlas.generation.pipeline import AnswerPipeline, QueryInput
from atlas.retrieval.service import RetrievalService, RetrievalSettings

router = APIRouter(prefix="/v1", tags=["query"])


@router.post("/search")
async def search(body: SearchRequest, ctx: ReadCtx, request: Request) -> dict[str, Any]:
    """Retrieval only, with per-stage ranks/scores and timings: the 'hit testing' playground,
    also used by Relay agents and the eval harness."""
    retrieval: RetrievalService = request.app.state.retrieval
    async with tenant_session(ctx.tenant_id) as s:
        colls = list((await s.execute(select(Collection).where(Collection.id.in_(body.collection_ids)))).scalars())
    if len(colls) != len(set(body.collection_ids)):
        raise NotFound("collection not found")
    settings = RetrievalSettings.for_collection(colls[0], RetrievalSettings())
    settings.mode, settings.rerank, settings.top_k = body.mode, body.rerank, body.top_k
    r = await retrieval.search(ctx.tenant_id, colls, body.query, user_groups=ctx.groups, settings=settings,
                               debug=body.debug)
    return {"results": [{
        "chunk_id": f.id, "document_id": f.payload.get("document_id"), "title": f.payload.get("title"),
        "section_path": f.payload.get("section_path"), "page_start": f.payload.get("page_start"),
        "page_end": f.payload.get("page_end"), "kind": f.payload.get("kind"), "text": f.payload.get("text"),
        "context": f.payload.get("context"), "fused_score": round(f.score, 5), "ranks": f.ranks,
        "scores": {k: round(v, 4) for k, v in f.raw.items()}} for f in r.items[: body.top_k]],
        "reranked": r.reranked, "timings_ms": {k: round(v, 2) for k, v in r.timings_ms.items()},
        **({"debug": r.debug} if body.debug else {})}


@router.post("/query", response_model=None)
async def query(body: QueryRequest, ctx: ReadCtx, request: Request) -> Any:
    pipeline: AnswerPipeline = request.app.state.pipeline
    qi = QueryInput(question=body.question, collection_ids=body.collection_ids, conversation_id=body.conversation_id,
                    mode=body.mode, top_k=body.top_k, use_cache=body.use_cache, use_memory=body.use_memory)
    if not body.stream:
        final: dict[str, Any] = {}
        sources: list[Any] = []
        async for ev in pipeline.run(ctx, qi):
            if ev.type == "sources":
                sources = ev.data["sources"]
            elif ev.type == "done":
                final = ev.data
        return {**final, "retrieved": sources}

    async def events() -> AsyncIterator[dict[str, str]]:
        try:
            async for ev in pipeline.run(ctx, qi):
                yield {"event": ev.type, "data": orjson.dumps(ev.data).decode()}
        except AtlasError as e:
            yield {"event": "error", "data": orjson.dumps({"code": e.code, "message": e.message}).decode()}

    return EventSourceResponse(events(), ping=15)


@router.get("/conversations")
async def conversations(ctx: Ctx, limit: int = 50) -> list[dict[str, Any]]:
    if ctx.user_id is None:
        return []
    async with tenant_session(ctx.tenant_id) as s:
        rows = (await s.execute(select(Conversation).where(Conversation.user_id == ctx.user_id)
                                .order_by(Conversation.updated_at.desc()).limit(min(limit, 200)))).scalars()
        return [{"id": str(c.id), "title": c.title, "summary": c.summary, "updated_at": c.updated_at.isoformat()}
                for c in rows]


@router.get("/conversations/{conv_id}/messages")
async def messages(conv_id: uuid.UUID, ctx: Ctx) -> list[dict[str, Any]]:
    async with tenant_session(ctx.tenant_id) as s:
        conv = await s.get(Conversation, conv_id)
        if conv is None or (ctx.user_id and conv.user_id != ctx.user_id):
            raise NotFound("conversation not found")
        rows = (await s.execute(select(Message).where(Message.conversation_id == conv_id).order_by(Message.seq)))
        return [{"id": str(m.id), "seq": m.seq, "role": m.role, "content": m.content,
                 "citations": m.citations.get("items", []), "verification": m.verification}
                for m in rows.scalars()]


@router.post("/feedback", status_code=201)
async def feedback(body: FeedbackRequest, ctx: Ctx) -> dict[str, str]:
    """Thumbs + corrections. Negative feedback and corrections are triage candidates for the eval set."""
    async with tenant_session(ctx.tenant_id) as s:
        msg = await s.get(Message, body.message_id)
        if msg is None:
            raise NotFound("message not found")
        trace_id = (msg.verification or {}).get("trace_id")
        fb = Feedback(id=uuid.uuid4(), tenant_id=uuid.UUID(ctx.tenant_id), message_id=body.message_id,
                      rating=body.rating, comment=body.comment, corrected_answer=body.corrected_answer)
        s.add(fb)
    if trace_id:  # link the human signal to the exact trace in Pulse (feeds the quality dashboards)
        import pulse

        try:
            await pulse.score(trace_id, "user_feedback", float(body.rating), comment=body.comment or None,
                              source="annotation")
        except Exception:  # noqa: S110 - feedback must never fail because tracing is down
            pass
    return {"id": str(fb.id), "trace_id": trace_id}
