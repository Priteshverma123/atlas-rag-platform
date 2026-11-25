"""Answer orchestration (the hot path). Emits a stream of typed events:

    meta → sources → token* → done            (or: meta → done for cache hits / abstentions)

    memory window + user facts ─┐
    query plan (heuristic|LLM) ─┴→ hybrid retrieval (per sub-query, parallel) → abstain?
        → context (budget, small-to-big) → LLM stream (cached prefix) → verify citations
        → persist turn → enqueue: memory extraction, summary roll → answer cache
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select

from atlas.common.errors import NotFound
from atlas.common.logging import get_logger
from atlas.common.telemetry import GenAI, observe, trace_context
from atlas.container import Container
from atlas.db.models import Chunk, Collection, Conversation
from atlas.db.session import tenant_session
from atlas.generation.citations import cited_numbers, verify
from atlas.guardrails import REFUSAL, check_input, check_output
from atlas.generation.prompts import (
    ABSTAIN_TEXT,
    ANSWER_SYSTEM,
    CHITCHAT_SYSTEM,
    PROMPT_VERSION,
    answer_user_message,
)
from atlas.kb.context import RequestContext
from atlas.memory.conversation import append, load_window
from atlas.memory.facts import FactStore
from atlas.retrieval.context import Source, build_context
from atlas.retrieval.fusion import Fused, rrf
from atlas.retrieval.planner import Intent, QueryPlan, plan_query
from atlas.retrieval.service import RetrievalService, RetrievalSettings
from pulse import Observation

log = get_logger(__name__)
Mode = Literal["fast", "standard", "deep"]
ROUTE_FOR_MODE = {"fast": "fast", "standard": "answer", "deep": "deep"}
Enqueue = Callable[..., Awaitable[Any]]


@dataclass(slots=True)
class QueryInput:
    question: str
    collection_ids: list[uuid.UUID]
    conversation_id: uuid.UUID | None = None
    mode: Mode = "standard"
    top_k: int | None = None
    use_cache: bool = True
    use_memory: bool = True


@dataclass(slots=True)
class Event:
    type: str
    data: dict[str, Any] = field(default_factory=dict)


class AnswerPipeline:
    def __init__(self, c: Container, retrieval: RetrievalService, facts: FactStore, enqueue: Enqueue) -> None:
        self.c, self.retrieval, self.facts, self.enqueue = c, retrieval, facts, enqueue
        s = c.settings
        self.defaults = RetrievalSettings(candidates=s.retrieval_candidates, top_k=s.retrieval_top_k)

    # ------------------------------------------------------------------ helpers
    async def _collections(self, ctx: RequestContext, ids: list[uuid.UUID]) -> list[Collection]:
        async with tenant_session(ctx.tenant_id) as s:
            rows = list((await s.execute(select(Collection).where(Collection.id.in_(ids)))).scalars())
        if len(rows) != len(set(ids)):
            raise NotFound("one or more collections not found")  # RLS hides other tenants' collections
        return rows

    async def _conversation(self, ctx: RequestContext, q: QueryInput) -> tuple[Conversation, list[dict[str, str]]]:
        async with tenant_session(ctx.tenant_id) as s:
            conv = await s.get(Conversation, q.conversation_id) if q.conversation_id else None
            if conv is None or (ctx.user_id and conv.user_id != ctx.user_id):
                if q.conversation_id:
                    raise NotFound("conversation not found")
                if ctx.user_id is None:
                    raise NotFound("a user is required for conversations")
                conv = Conversation(id=uuid.uuid4(), tenant_id=uuid.UUID(ctx.tenant_id), user_id=ctx.user_id,
                                    collection_ids=[str(i) for i in q.collection_ids], title=q.question[:200])
                s.add(conv)
                await s.flush()
                return conv, []
            window = await load_window(s, conv, self.c.settings.memory_buffer_tokens)
            return conv, window

    def _cache_ns(self, ctx: RequestContext, q: QueryInput) -> str:
        raw = f"{ctx.tenant_id}|{sorted(map(str, q.collection_ids))}|{sorted(ctx.groups)}|{PROMPT_VERSION}|{q.mode}"
        return "answer:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

    async def _retrieve(self, ctx: RequestContext, colls: list[Collection], plan: QueryPlan,
                        settings: RetrievalSettings) -> tuple[list[Fused], dict[str, float], bool]:
        queries = [plan.standalone_query, *plan.sub_queries] if plan.intent == Intent.MULTI_HOP else \
            [plan.standalone_query]
        kinds = ["summary", "text", "table", "figure"] if plan.intent == Intent.GLOBAL else None
        results = await asyncio.gather(*(self.retrieval.search(ctx.tenant_id, colls, qq, user_groups=ctx.groups,
                                                               settings=settings, kinds=kinds) for qq in queries))
        if len(results) == 1:
            r = results[0]
            return r.items, r.timings_ms, r.reranked
        # multi-hop: fuse the per-sub-query rankings so each sub-question contributes evidence
        fused = rrf({f"q{i}": [(f.id, f.score, f.payload) for f in r.items[: settings.candidates]]
                     for i, r in enumerate(results)})
        timings = {"total": max(r.timings_ms.get("total", 0) for r in results)}
        return fused, timings, any(r.reranked for r in results)

    async def _parents(self, ctx: RequestContext, items: list[Fused]) -> dict[str, str]:
        pids = {f.payload.get("parent_id") for f in items if f.payload.get("parent_id")}
        if not pids:
            return {}
        async with tenant_session(ctx.tenant_id) as s:
            rows = (await s.execute(select(Chunk.id, Chunk.text).where(
                Chunk.id.in_([uuid.UUID(p) for p in pids if p])))).all()
        return {str(i): t for i, t in rows}

    # ------------------------------------------------------------------ main entry
    async def run(self, ctx: RequestContext, q: QueryInput) -> AsyncIterator[Event]:
        """Traced end to end: ask → guardrail.input → memory.recall ‖ route → retrieval.search →
        context.assemble → (gateway) generation → guardrail.output → citations.verify."""
        with trace_context(user_id=ctx.user_external_id or str(ctx.user_id or ""), tags=["atlas", q.mode],
                           name="ask", metadata={"tenant_id": ctx.tenant_id}), \
                observe("ask", type="span", input=q.question, **{"atlas.mode": q.mode, GenAI.PROMPT_NAME: "answer",
                                                                  GenAI.PROMPT_VERSION: PROMPT_VERSION}) as root:
            root.set_trace_io(input=q.question)
            final: dict[str, Any] = {}
            async for ev in self._run(ctx, q, root):
                if ev.type == "done":
                    ev.data["trace_id"] = root.trace_id
                    final = ev.data
                yield ev
            answer = final.get("answer", "")
            root.set_output(answer).set_trace_io(output=answer)
            if final.get("blocked"):
                root.warn("blocked by input guardrail")

    async def _run(self, ctx: RequestContext, q: QueryInput, root: Observation) -> AsyncIterator[Event]:
        t0 = time.perf_counter()
        timings: dict[str, float] = {}

        # ---- input guardrail (deterministic, before anything costs money)
        with observe("guardrail.input", type="guardrail", input=q.question) as g:
            gin = check_input(q.question)
            g.set_output(gin.to_dict())
            if gin.decision != "allow":
                g.warn(", ".join(gin.reasons + [f"pii:{p}" for p in gin.pii]) or gin.decision)
        colls = await self._collections(ctx, q.collection_ids)
        conv, window = await self._conversation(ctx, q)
        root.span.set_attribute(GenAI.CONVERSATION_ID, str(conv.id))
        root.span.set_attribute("pulse.session_id", str(conv.id))
        with trace_context(session_id=str(conv.id)):
            if gin.decision == "block":
                await self._persist(ctx, conv, q.question, REFUSAL, [], {"guardrail": gin.to_dict()}, root.trace_id)
                yield Event("meta", {"conversation_id": str(conv.id), "intent": "blocked"})
                yield Event("done", {"answer": REFUSAL, "citations": [], "blocked": True, "guardrail": gin.to_dict(),
                                     "conversation_id": str(conv.id),
                                     "timings_ms": {"total": (time.perf_counter() - t0) * 1000}})
                return

            # ---- memory + planning in parallel
            async def recall() -> list[str]:
                with observe("memory.recall", type="retriever", input=q.question) as m:
                    if not (q.use_memory and ctx.user_id):
                        m.set_output([])
                        return []
                    try:
                        facts = await self.facts.relevant(ctx.tenant_id, str(ctx.user_id), q.question,
                                                          self.c.settings.memory_facts_top_k)
                    except Exception as exc:
                        m.warn(f"memory unavailable: {exc}")
                        return []
                    m.set_output({"facts": facts, "summary_chars": len(conv.summary or ""),
                                  "window_turns": len(window)})
                    return facts

            facts_task = asyncio.create_task(recall())
            with observe("route", type="span", input=q.question) as r:
                plan, used_llm = await plan_query(self.c.llm, ctx.tenant_id, q.question, window, conv.summary)
                r.set_output(plan.model_dump()).set_metadata(planner_llm=used_llm)
            profile: list[str] = await facts_task
            timings["plan"] = (time.perf_counter() - t0) * 1000
            yield Event("meta", {"conversation_id": str(conv.id), "intent": plan.intent.value,
                                 "standalone_query": plan.standalone_query, "planner_llm": used_llm,
                                 "guardrail": gin.to_dict(), "trace_id": root.trace_id})

            # ---- answer cache (first turns only: later turns depend on conversation state)
            cache_ns = self._cache_ns(ctx, q)
            if q.use_cache and not window and plan.intent != Intent.CHITCHAT:
                with observe("cache.lookup", type="span", input=plan.standalone_query) as cl:
                    try:
                        hit = await self.c.answer_cache.lookup(cache_ns, plan.standalone_query, 0.97, 3600)
                    except Exception:
                        hit = None
                    cl.set_output({"hit": bool(hit), "score": hit[1] if hit else None})
                if hit:
                    value, score = hit
                    await self._persist(ctx, conv, q.question, value["answer"], value.get("citations", []),
                                        value.get("verification", {}), root.trace_id)
                    yield Event("done", {**value, "cache": f"semantic:{score:.3f}", "conversation_id": str(conv.id),
                                         "timings_ms": {"total": (time.perf_counter() - t0) * 1000}})
                    return

            route = ROUTE_FOR_MODE[q.mode]
            if plan.intent == Intent.CHITCHAT:
                msgs = [{"role": "system", "content": CHITCHAT_SYSTEM}, *window, {"role": "user", "content": q.question}]
                res = await self.c.llm.chat("fast", msgs, tenant_id=ctx.tenant_id, max_tokens=200)
                await self._persist(ctx, conv, q.question, res.text, [], {}, root.trace_id)
                yield Event("done", {"answer": res.text, "citations": [], "conversation_id": str(conv.id),
                                     "timings_ms": {"total": (time.perf_counter() - t0) * 1000}})
                return

            # ---- retrieval
            settings = RetrievalSettings.for_collection(colls[0], self.defaults)
            if q.top_k:
                settings.top_k = q.top_k
            t1 = time.perf_counter()
            items, rtimings, reranked = await self._retrieve(ctx, colls, plan, settings)
            timings["retrieval"] = (time.perf_counter() - t1) * 1000
            timings.update({f"retrieval.{k}": v for k, v in rtimings.items()})

            top = items[: settings.top_k * 2]
            top_rerank = top[0].raw.get("rerank") if top and reranked else None
            if not top or (top_rerank is not None and top_rerank < self.c.settings.abstain_threshold):
                with observe("abstain", type="evaluator") as ab:
                    ab.set_output({"candidates": len(top), "top_rerank": top_rerank,
                                   "threshold": self.c.settings.abstain_threshold}).score("abstained", 1.0)
                await self._persist(ctx, conv, q.question, ABSTAIN_TEXT, [], {"abstained": True}, root.trace_id)
                yield Event("done", {"answer": ABSTAIN_TEXT, "citations": [], "abstained": True,
                                     "conversation_id": str(conv.id), "timings_ms": timings})
                return

            with observe("context.assemble", type="span", input={"candidates": len(top)}) as ca:
                parents = await self._parents(ctx, top)
                sources = build_context(top, self.c.settings.context_token_budget, parents,
                                        max_sources=settings.top_k)
                ca.set_output([{"n": s.n, "title": s.title, "section": s.section_path,
                                "pages": [s.page_start, s.page_end], "score": round(s.score, 4)} for s in sources])
                ca.set_metadata(budget_tokens=self.c.settings.context_token_budget, parents=len(parents))
            yield Event("sources", {"sources": [s.citation() for s in sources]})

            # ---- generation (streamed; the gateway records the 'generation' observation)
            user_msg = answer_user_message(plan.standalone_query, "\n\n".join(s.render() for s in sources),
                                           profile, conv.summary)
            messages = [{"role": "system", "content": ANSWER_SYSTEM}, *window, {"role": "user", "content": user_msg}]
            tags = sorted({f"doc:{s.document_id}" for s in sources})
            parts: list[str] = []
            meta: dict[str, Any] = {}
            t2 = time.perf_counter()
            first_token_ms = None
            async for d in self.c.llm.stream(route, messages, tenant_id=ctx.tenant_id, cache_tags=tags):
                if d.text:
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - t0) * 1000
                    parts.append(d.text)
                    yield Event("token", {"text": d.text})
                if d.done:
                    meta = d.meta
            timings["generation"] = (time.perf_counter() - t2) * 1000
            timings["ttft"] = first_token_ms or 0.0
            answer = "".join(parts).strip()

            # ---- output guardrail
            with observe("guardrail.output", type="guardrail", input=answer) as go:
                gout = check_output(answer, ANSWER_SYSTEM)
                go.set_output(gout.to_dict())
                if gout.decision == "block":
                    go.warn(", ".join(gout.reasons))
                    answer = REFUSAL
                elif gout.decision == "flag":
                    go.warn("pii in output: " + ", ".join(gout.pii))

            # ---- citation verification (scores attached to the trace)
            with observe("citations.verify", type="evaluator", input={"sources": len(sources)}) as ev:
                used = cited_numbers(answer)
                citations = [s.citation() for s in sources if s.n in used]
                v = verify(answer, sources)
                ev.set_output(v.to_dict())
                ev.score("support_ratio", v.supported_ratio)
                ev.score("citations", float(len(citations)))
                if v.invalid_markers or v.supported_ratio < 0.5:
                    ev.warn(f"weak grounding: supported_ratio={v.supported_ratio:.2f}")
            msg_id = await self._persist(ctx, conv, q.question, answer, citations, v.to_dict(), root.trace_id)
            timings["total"] = (time.perf_counter() - t0) * 1000
            root.set_metadata(intent=plan.intent.value, reranked=reranked, ttft_ms=round(timings["ttft"], 1),
                              sources=len(sources))

            result = {"answer": answer, "citations": citations, "verification": v.to_dict(),
                      "conversation_id": str(conv.id), "message_id": str(msg_id), "timings_ms": timings,
                      "usage": meta.get("usage"), "provider": meta.get("provider"), "cost_usd": meta.get("cost_usd"),
                      "intent": plan.intent.value, "reranked": reranked, "guardrail": gin.to_dict()}
            if q.use_cache and not window and v.supported_ratio >= 0.8 and not v.invalid_markers:
                try:
                    await self.c.answer_cache.store(cache_ns, plan.standalone_query,
                                                    {"answer": answer, "citations": citations,
                                                     "verification": v.to_dict()}, tags)
                except Exception:
                    log.warning("answer_cache_store_failed", exc_info=True)
            yield Event("done", result)

    async def _persist(self, ctx: RequestContext, conv: Conversation, question: str, answer: str,
                       citations: list[dict[str, Any]], verification: dict[str, Any],
                       trace_id: str | None = None) -> uuid.UUID:
        verification = {**verification, "trace_id": trace_id} if trace_id else verification
        async with tenant_session(ctx.tenant_id) as s:
            conv = await s.get(Conversation, conv.id) or conv
            user_msg = await append(s, conv, "user", question)
            msg = await append(s, conv, "assistant", answer, {"items": citations}, verification)
        try:
            await self.enqueue("roll_summary", ctx.tenant_id, str(conv.id))
            if ctx.user_id:
                await self.enqueue("extract_memories", ctx.tenant_id, str(ctx.user_id), str(user_msg.id))
        except Exception:
            log.warning("enqueue_failed", exc_info=True)  # memory is best-effort; never fail the answer
        return msg.id


def sources_from_items(items: list[Fused], budget: int, k: int) -> list[Source]:
    return build_context(items, budget, {}, max_sources=k)
