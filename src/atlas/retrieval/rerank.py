"""Cross-encoder reranking via TEI ``/rerank``, with adaptive skipping.

Reranking is the single biggest retrieval-latency cost (research: 2–4× when untuned), so it is
skipped when fusion is already confident: the top candidate was ranked #1 by *both* retrievers.
The skip rate and its nDCG impact are reported by the eval harness.
"""

from __future__ import annotations

import asyncio

import httpx

from atlas.common.logging import get_logger
from atlas.common.telemetry import span
from atlas.retrieval.fusion import Fused

log = get_logger(__name__)


def confident(fused: list[Fused], min_dense: float = 0.72) -> bool:
    """Skip the reranker only when both retrievers put the same chunk first AND it is semantically close.
    Agreement alone isn't enough: a query sharing one rare keyword ("price of the KD-200") makes both
    retrievers agree on an irrelevant chunk; reranking then supplies the relevance score abstention needs."""
    if len(fused) < 2:
        return True
    top = fused[0]
    return top.ranks.get("dense") == 1 and top.ranks.get("sparse") == 1 and top.raw.get("dense", 0.0) >= min_dense


class Reranker:
    def __init__(self, url: str, timeout_s: float = 30.0, max_chars: int = 700, batch: int = 32) -> None:
        self.url = url.rstrip("/")
        self.max_chars = max_chars
        self.batch = batch
        self._http = httpx.AsyncClient(timeout=timeout_s)

    async def rerank(self, query: str, items: list[Fused]) -> list[Fused] | None:
        """Returns items re-sorted with ``raw['rerank']`` set, or None if the reranker is down
        (retrieval degrades gracefully to fused order instead of failing the request)."""
        if not items:
            return items
        texts = [((f.payload.get("context") or "") + "\n" + f.payload.get("text", ""))[: self.max_chars]
                 for f in items]

        async def score(offset: int, chunk: list[str]) -> list[tuple[int, float]]:
            r = await self._http.post(f"{self.url}/rerank", json={"query": query, "texts": chunk, "truncate": True})
            r.raise_for_status()
            return [(offset + row["index"], float(row["score"])) for row in r.json()]

        with span("retrieval.rerank", kind="span", **{"rerank.candidates": len(items)}):
            # the server caps request size (TEI max_client_batch_size): split, score concurrently, re-stitch
            try:
                parts = await asyncio.gather(*(score(i, texts[i:i + self.batch])
                                               for i in range(0, len(texts), self.batch)))
            except httpx.HTTPError as exc:
                log.warning("rerank_unavailable", error=str(exc))
                return None
        for part in parts:
            for idx, sc in part:
                items[idx].raw["rerank"] = sc
        return sorted(items, key=lambda f: -f.raw.get("rerank", float("-inf")))

    async def aclose(self) -> None:
        await self._http.aclose()
