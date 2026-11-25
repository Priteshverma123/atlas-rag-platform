"""Hybrid retrieval service: dense ‖ BM25 → RRF → (adaptive) rerank → threshold.

Per-collection settings (Dify-style) override defaults:
    {"weights": {"dense": 1.0, "sparse": 1.0}, "candidates": 50, "top_k": 6,
     "rerank": true, "score_threshold": 0.0}
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from qdrant_client import models

from atlas.common.telemetry import span
from atlas.container import Container
from atlas.db.models import Collection
from atlas.index.qdrant_store import QdrantStore
from atlas.retrieval.fusion import Fused, rrf
from atlas.retrieval.rerank import Reranker, confident


@dataclass(slots=True)
class RetrievalSettings:
    weights: dict[str, float] = field(default_factory=lambda: {"dense": 1.0, "sparse": 1.0})
    candidates: int = 50
    top_k: int = 6
    rerank: bool = True
    score_threshold: float = 0.0  # applied to rerank score when reranking ran
    mode: str = "hybrid"  # hybrid | dense | sparse (for ablations)

    @classmethod
    def for_collection(cls, coll: Collection, defaults: RetrievalSettings) -> RetrievalSettings:
        cfg = coll.retrieval or {}
        return cls(weights=cfg.get("weights", defaults.weights), candidates=cfg.get("candidates", defaults.candidates),
                   top_k=cfg.get("top_k", defaults.top_k), rerank=cfg.get("rerank", defaults.rerank),
                   score_threshold=cfg.get("score_threshold", defaults.score_threshold),
                   mode=cfg.get("mode", defaults.mode))


@dataclass(slots=True)
class RetrievalResult:
    items: list[Fused]
    timings_ms: dict[str, float]
    reranked: bool
    debug: dict[str, Any] = field(default_factory=dict)


class RetrievalService:
    def __init__(self, c: Container) -> None:
        self.c = c
        s = c.settings
        self.rerank_candidates = s.rerank_candidates
        self.reranker = Reranker(s.rerank_url, max_chars=s.rerank_max_chars, batch=s.rerank_batch)             if s.rerank_enabled else None

    async def search(self, tenant_id: str, collections: list[Collection], query: str, *,
                     user_groups: list[str] | None, settings: RetrievalSettings, kinds: list[str] | None = None,
                     debug: bool = False) -> RetrievalResult:
        timings: dict[str, float] = {}
        t0 = time.perf_counter()
        with span("retrieval.search", kind="retriever", **{"retrieval.collections": len(collections), "pulse.input": query}) as sp:
            dense_vec = await self.c.embedder.embed_one(query, kind="query")
            sparse_vec = self.c.bm25.encode_query(query)
            timings["encode"] = (time.perf_counter() - t0) * 1000

            t1 = time.perf_counter()
            kind_filter = [models.FieldCondition(key="kind", match=models.MatchAny(any=kinds))] if kinds else \
                [models.FieldCondition(key="kind", match=models.MatchAny(any=["text", "table", "figure", "code"]))]
            searches = []
            for coll in collections:
                flt = QdrantStore.security_filter(tenant_id, str(coll.id), user_groups, kind_filter)
                searches.append(self.c.store.search(coll.qdrant_collection, dense_vec, sparse_vec, flt,
                                                    settings.candidates))
            results = await asyncio.gather(*searches, return_exceptions=True)
            dense_hits, sparse_hits = [], []
            for r in results:
                if isinstance(r, BaseException):
                    continue  # a missing/empty collection must not fail the whole query
                dense_hits.extend(r[0])
                sparse_hits.extend(r[1])
            dense_hits.sort(key=lambda h: -h.score)
            sparse_hits.sort(key=lambda h: -h.score)
            timings["search"] = (time.perf_counter() - t1) * 1000

            rankings: dict[str, list[tuple[str, float, dict[str, Any]]]] = {}
            if settings.mode in ("hybrid", "dense"):
                rankings["dense"] = [(h.id, h.score, h.payload) for h in dense_hits[: settings.candidates]]
            if settings.mode in ("hybrid", "sparse"):
                rankings["sparse"] = [(h.id, h.score, h.payload) for h in sparse_hits[: settings.candidates]]
            fused = rrf(rankings, weights=settings.weights)[: settings.candidates]

            reranked = False
            t2 = time.perf_counter()
            if self.reranker and settings.rerank and fused and not confident(fused):
                head, tail = fused[: self.rerank_candidates], fused[self.rerank_candidates:]
                out = await self.reranker.rerank(query, head)
                if out is not None:
                    fused, reranked = out + tail, True
                    if settings.score_threshold:
                        fused = [f for f in fused if f.raw.get("rerank", 0.0) >= settings.score_threshold]
            timings["rerank"] = (time.perf_counter() - t2) * 1000
            timings["total"] = (time.perf_counter() - t0) * 1000
            sp.set_attribute("retrieval.reranked", reranked)
            sp.set_attribute("retrieval.dense_hits", len(dense_hits))
            sp.set_attribute("retrieval.sparse_hits", len(sparse_hits))
            import orjson

            sp.set_attribute("pulse.output", orjson.dumps([
                {"title": f.payload.get("title"), "section": f.payload.get("section_path"),
                 "pages": [f.payload.get("page_start"), f.payload.get("page_end")], "ranks": f.ranks,
                 "scores": {k: round(v, 4) for k, v in f.raw.items()}} for f in fused[:8]]).decode())
            sp.set_attribute("pulse.metadata.timings_ms", orjson.dumps(
                {k: round(v, 1) for k, v in timings.items()}).decode())

        dbg: dict[str, Any] = {}
        if debug:
            dbg = {"dense": [(h.id, round(h.score, 4)) for h in dense_hits[:20]],
                   "sparse": [(h.id, round(h.score, 4)) for h in sparse_hits[:20]],
                   "query_terms": len(sparse_vec.indices)}
        return RetrievalResult(items=fused, timings_ms=timings, reranked=reranked, debug=dbg)

    async def aclose(self) -> None:
        if self.reranker:
            await self.reranker.aclose()
