"""Semantic cache in Qdrant.

Rules (from Portkey/Bifrost production experience, see docs/adr/0007-cache-layers.md):
  * only consulted after an exact-cache miss;
  * only for short conversations (≤ N messages) without tools: long or agentic conversations
    rarely repeat and false hits there are costly;
  * namespaced by tenant + route + hash(system prompt) so different instructions never collide;
  * each entry stores the embedding model id; a model change can't return garbage matches;
  * entries carry tags (source document ids) for invalidation, and a TTL via ``created_at``.

The same class is reused by the Atlas API as an *answer cache* (namespace = tenant + collection
set + ACL hash + prompt version).
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from atlas.common.logging import get_logger
from atlas.embeddings.client import Embedder

log = get_logger(__name__)
COLLECTION = "atlas_semantic_cache"


class SemanticCache:
    def __init__(self, qdrant: AsyncQdrantClient, embedder: Embedder) -> None:
        self.q = qdrant
        self.embedder = embedder
        self._ready = False

    async def ensure(self) -> None:
        if self._ready:
            return
        try:
            await self._create()
        except Exception:
            # another process created it concurrently; fine as long as it exists now
            if not await self.q.collection_exists(COLLECTION):
                raise
        self._ready = True

    async def _create(self) -> None:
        if not await self.q.collection_exists(COLLECTION):
            await self.q.create_collection(
                COLLECTION, vectors_config=models.VectorParams(size=self.embedder.dim, distance=models.Distance.COSINE))
            for field, schema in (("ns", models.PayloadSchemaType.KEYWORD), ("tags", models.PayloadSchemaType.KEYWORD),
                                  ("embed_model", models.PayloadSchemaType.KEYWORD),
                                  ("created_at", models.PayloadSchemaType.FLOAT)):
                await self.q.create_payload_index(COLLECTION, field, field_schema=schema)

    async def lookup(self, ns: str, text: str, threshold: float, ttl_s: int) -> tuple[dict[str, Any], float] | None:
        await self.ensure()
        vec = await self.embedder.embed_one(text, kind="query")
        flt = models.Filter(must=[
            models.FieldCondition(key="ns", match=models.MatchValue(value=ns)),
            models.FieldCondition(key="embed_model", match=models.MatchValue(value=self.embedder.model)),
            models.FieldCondition(key="created_at", range=models.Range(gte=time.time() - ttl_s)),
        ])
        res = await self.q.query_points(COLLECTION, query=vec, query_filter=flt, limit=1, with_payload=True,
                                        score_threshold=threshold)
        if not res.points:
            return None
        p = res.points[0]
        return dict(p.payload or {}).get("value", {}), float(p.score)

    async def store(self, ns: str, text: str, value: dict[str, Any], tags: list[str]) -> None:
        await self.ensure()
        vec = await self.embedder.embed_one(text, kind="query")
        await self.q.upsert(COLLECTION, points=[models.PointStruct(
            id=str(uuid.uuid4()), vector=vec,
            payload={"ns": ns, "value": value, "tags": tags, "embed_model": self.embedder.model,
                     "created_at": time.time(), "text": text[:500]})])

    async def invalidate_tags(self, tags: list[str]) -> None:
        if not tags:
            return
        await self.ensure()
        await self.q.delete(COLLECTION, points_selector=models.FilterSelector(filter=models.Filter(
            must=[models.FieldCondition(key="tags", match=models.MatchAny(any=tags))])))
