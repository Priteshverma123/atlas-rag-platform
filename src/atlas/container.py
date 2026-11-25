"""Process-wide service container (built once per API/worker process, closed on shutdown).

Plain composition, no DI framework: every dependency is visible here, and tests can construct a
container with fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

from qdrant_client import AsyncQdrantClient
from redis.asyncio import Redis

from atlas.common.settings import Settings, get_settings
from atlas.embeddings.client import Embedder
from atlas.embeddings.sparse import BM25Encoder
from atlas.gateway.cache.semantic import SemanticCache
from atlas.index.qdrant_store import QdrantStore
from atlas.kb.objects import ObjectStore
from atlas.llm.client import LLMClient


@dataclass
class Container:
    settings: Settings
    redis: Redis
    qdrant: AsyncQdrantClient
    store: QdrantStore
    embedder: Embedder
    bm25: BM25Encoder
    llm: LLMClient
    objects: ObjectStore
    answer_cache: SemanticCache

    @classmethod
    def build(cls, settings: Settings | None = None) -> Container:
        s = settings or get_settings()
        redis = Redis.from_url(s.redis_url, decode_responses=False)
        qdrant = AsyncQdrantClient(url=s.qdrant_url,
                                   api_key=s.qdrant_api_key.get_secret_value() if s.qdrant_api_key else None)
        embedder = Embedder(s, redis)
        return cls(settings=s, redis=redis, qdrant=qdrant, store=QdrantStore(qdrant), embedder=embedder,
                   bm25=BM25Encoder(), llm=LLMClient(s), objects=ObjectStore(s),
                   answer_cache=SemanticCache(qdrant, embedder))

    async def aclose(self) -> None:
        await self.llm.aclose()
        await self.embedder.aclose()
        await self.qdrant.close()
        await self.redis.aclose()
