"""Dense embedding client for Hugging Face Text-Embeddings-Inference (TEI).

* Content-hash cache in Redis, keyed by **model id** (vectors from different models must never
  mix: a classic silent bug) and input type (query vs document).
* float16 storage halves cache memory; cosine similarity is unaffected in practice.
* Batches by size, bounded concurrency, single round trip MGET for cache lookups.

When Lumen's Embedding Service is deployed, point ``ATLAS_EMBED_URL`` at it: same contract.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

import httpx
import numpy as np
from redis.asyncio import Redis

from atlas.common.errors import UpstreamError
from atlas.common.settings import Settings
from atlas.common.telemetry import span
from atlas.common.text import normalize_text, sha256_text

# Instruction prefixes recommended by the BGE model card for asymmetric retrieval.
_QUERY_PREFIX = {"BAAI/bge-small-en-v1.5": "Represent this sentence for searching relevant passages: ",
                 "BAAI/bge-base-en-v1.5": "Represent this sentence for searching relevant passages: ",
                 "BAAI/bge-large-en-v1.5": "Represent this sentence for searching relevant passages: "}


class Embedder:
    def __init__(self, settings: Settings, redis: Redis | None, *, ttl_s: int = 7 * 86400,
                 concurrency: int = 4) -> None:
        self.url = settings.embed_url.rstrip("/")
        self.model = settings.embed_model
        self.dim = settings.embed_dim
        self.batch = settings.embed_batch_size
        self.redis = redis
        self.ttl_s = ttl_s
        self._sem = asyncio.Semaphore(max(concurrency, settings.embed_concurrency))
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))
        self.stats = {"hits": 0, "misses": 0}

    def _key(self, text: str, kind: str) -> str:
        return f"emb:{self.model}:{kind}:{sha256_text(text)}"

    async def _call(self, inputs: list[str]) -> list[list[float]]:
        async with self._sem:
            try:
                r = await self._http.post(f"{self.url}/embed",
                                          json={"inputs": inputs, "normalize": True, "truncate": True})
                r.raise_for_status()
            except httpx.HTTPError as exc:
                raise UpstreamError(f"embedding server error: {exc}") from exc
            return r.json()  # type: ignore[no-any-return]

    async def embed(self, texts: Sequence[str], kind: str = "document") -> list[list[float]]:
        if not texts:
            return []
        prefix = _QUERY_PREFIX.get(self.model, "") if kind == "query" else ""
        norm = [prefix + normalize_text(t) for t in texts]
        out: list[list[float] | None] = [None] * len(norm)
        with span("embed", kind="embedding", **{"embed.model": self.model, "embed.count": len(norm)}) as s:
            if self.redis is not None:
                cached = await self.redis.mget([self._key(t, kind) for t in norm])
                for i, raw in enumerate(cached):
                    if raw is not None:
                        out[i] = np.frombuffer(raw, dtype=np.float16).astype(np.float32).tolist()
            missing = [i for i, v in enumerate(out) if v is None]
            self.stats["hits"] += len(norm) - len(missing)
            self.stats["misses"] += len(missing)
            s.set_attribute("embed.cache_hits", len(norm) - len(missing))
            # dedupe identical texts inside one call
            uniq: dict[str, list[int]] = {}
            for i in missing:
                uniq.setdefault(norm[i], []).append(i)
            texts_to_call = list(uniq)
            batches = [texts_to_call[i:i + self.batch] for i in range(0, len(texts_to_call), self.batch)]
            results = await asyncio.gather(*(self._call(b) for b in batches))
            pipe = self.redis.pipeline() if self.redis is not None else None
            for batch, vecs in zip(batches, results, strict=True):
                for t, v in zip(batch, vecs, strict=True):
                    for i in uniq[t]:
                        out[i] = v
                    if pipe is not None:
                        pipe.set(self._key(t, kind), np.asarray(v, dtype=np.float16).tobytes(), ex=self.ttl_s)
            if pipe is not None and texts_to_call:
                await pipe.execute()
        return [v for v in out if v is not None]

    async def embed_one(self, text: str, kind: str = "query") -> list[float]:
        return (await self.embed([text], kind))[0]

    async def aclose(self) -> None:
        await self._http.aclose()
