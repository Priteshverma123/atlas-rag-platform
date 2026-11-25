"""Qdrant access layer: one physical collection per Atlas collection, addressed via an alias.

* named vectors: ``dense`` (cosine, int8 scalar quantization with rescoring) + ``bm25`` sparse
  (``Modifier.IDF``);
* tenant_id / collection_id / document_id / acl_groups payload indexes; the tenant filter is
  injected server-side on *every* query (defence in depth on top of per-collection isolation);
* blue/green re-index: build ``<alias>__v<N+1>``, evaluate, then atomically swap the alias.

Scaling note (ADR-0010): per-collection physical collections give clean blue/green swaps; at
thousands of tenants switch to a shared collection with ``is_tenant`` payload partitioning.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Any

from qdrant_client import AsyncQdrantClient, models

from atlas.embeddings.sparse import SparseVec

DENSE, SPARSE = "dense", "bm25"


@dataclass(slots=True)
class Hit:
    id: str
    score: float
    payload: dict[str, Any]


def point_id(document_id: uuid.UUID | str, version: int, ordinal: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{version}:{ordinal}"))


class QdrantStore:
    def __init__(self, client: AsyncQdrantClient) -> None:
        self.q = client

    @staticmethod
    def alias_for(collection_id: uuid.UUID | str) -> str:
        return f"atlas_{uuid.UUID(str(collection_id)).hex}"

    async def ensure_collection(self, alias: str, dim: int) -> str:
        aliases = {a.alias_name: a.collection_name for a in (await self.q.get_aliases()).aliases}
        if alias in aliases:
            return aliases[alias]
        physical = f"{alias}__v1"
        await self._create_physical(physical, dim)
        await self.q.update_collection_aliases(change_aliases_operations=[
            models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=physical, alias_name=alias))])
        return physical

    async def _create_physical(self, name: str, dim: int) -> None:
        if await self.q.collection_exists(name):
            return
        await self.q.create_collection(
            name,
            vectors_config={DENSE: models.VectorParams(size=dim, distance=models.Distance.COSINE, on_disk=False)},
            sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
            quantization_config=models.ScalarQuantization(scalar=models.ScalarQuantizationConfig(
                type=models.ScalarType.INT8, quantile=0.99, always_ram=True)),
            hnsw_config=models.HnswConfigDiff(m=16, ef_construct=128),
        )
        for field, schema in (("tenant_id", models.PayloadSchemaType.KEYWORD),
                              ("collection_id", models.PayloadSchemaType.KEYWORD),
                              ("document_id", models.PayloadSchemaType.KEYWORD),
                              ("acl_groups", models.PayloadSchemaType.KEYWORD),
                              ("kind", models.PayloadSchemaType.KEYWORD),
                              ("version", models.PayloadSchemaType.INTEGER)):
            await self.q.create_payload_index(name, field, field_schema=schema)

    async def upsert(self, alias: str, points: list[tuple[str, list[float], SparseVec, dict[str, Any]]],
                     batch: int = 256) -> None:
        """Pipelined: intermediate batches don't wait for indexing; the final batch waits, and because a
        shard applies operations in order, every earlier batch is visible once it returns."""
        for i in range(0, len(points), batch):
            last = i + batch >= len(points)
            await self.q.upsert(alias, wait=last, points=[
                models.PointStruct(id=pid, payload=payload, vector={
                    DENSE: dense, SPARSE: models.SparseVector(indices=sp.indices, values=sp.values)})
                for pid, dense, sp, payload in points[i:i + batch]])

    async def delete_document(self, alias: str, document_id: str, *, keep_version: int | None = None) -> None:
        must: list[models.Condition] = [models.FieldCondition(key="document_id",
                                                              match=models.MatchValue(value=document_id))]
        must_not: list[models.Condition] = []
        if keep_version is not None:
            must_not.append(models.FieldCondition(key="version", match=models.MatchValue(value=keep_version)))
        await self.q.delete(alias, wait=True, points_selector=models.FilterSelector(
            filter=models.Filter(must=must, must_not=must_not)))

    async def set_payload(self, alias: str, document_id: str, payload: dict[str, Any]) -> None:
        await self.q.set_payload(alias, payload=payload, points=models.Filter(must=[
            models.FieldCondition(key="document_id", match=models.MatchValue(value=document_id))]))

    @staticmethod
    def security_filter(tenant_id: str, collection_id: str, user_groups: list[str] | None,
                        extra: list[models.Condition] | None = None) -> models.Filter:
        """Tenant + collection are hard requirements; ACL: public chunks or group intersection."""
        must: list[models.Condition] = [
            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
            models.FieldCondition(key="collection_id", match=models.MatchValue(value=collection_id)),
            *(extra or []),
        ]
        acl: list[models.Condition] = [models.IsEmptyCondition(is_empty=models.PayloadField(key="acl_groups"))]
        if user_groups:
            acl.append(models.FieldCondition(key="acl_groups", match=models.MatchAny(any=user_groups)))
        must.append(models.Filter(should=acl))
        return models.Filter(must=must)

    async def search(self, alias: str, dense: list[float], sparse: SparseVec, flt: models.Filter, limit: int,
                     ef: int = 128) -> tuple[list[Hit], list[Hit]]:
        """Dense and sparse searches run concurrently; fusion happens client-side (fusion.py) so the
        hit-testing API can show every stage's ranks and scores."""
        params = models.SearchParams(hnsw_ef=ef, quantization=models.QuantizationSearchParams(
            rescore=True, oversampling=2.0))
        dense_q = self.q.query_points(alias, query=dense, using=DENSE, query_filter=flt, limit=limit,
                                      with_payload=True, search_params=params)
        sparse_q = self.q.query_points(alias, query=models.SparseVector(indices=sparse.indices, values=sparse.values),
                                       using=SPARSE, query_filter=flt, limit=limit, with_payload=True)
        d, s = await asyncio.gather(dense_q, sparse_q)
        conv = lambda res: [Hit(str(p.id), float(p.score), dict(p.payload or {})) for p in res.points]  # noqa: E731
        return conv(d), conv(s)

    async def count(self, alias: str, flt: models.Filter) -> int:
        return (await self.q.count(alias, count_filter=flt, exact=True)).count

    async def create_next_version(self, alias: str, dim: int) -> str:
        current = {a.alias_name: a.collection_name for a in (await self.q.get_aliases()).aliases}.get(alias)
        n = int(current.rsplit("__v", 1)[1]) + 1 if current and "__v" in current else 1
        physical = f"{alias}__v{n}"
        await self._create_physical(physical, dim)
        return physical

    async def swap_alias(self, alias: str, new_physical: str) -> str | None:
        current = {a.alias_name: a.collection_name for a in (await self.q.get_aliases()).aliases}.get(alias)
        ops: list[Any] = []
        if current:
            ops.append(models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias)))
        ops.append(models.CreateAliasOperation(create_alias=models.CreateAlias(collection_name=new_physical,
                                                                                alias_name=alias)))
        await self.q.update_collection_aliases(change_aliases_operations=ops)  # atomic
        return current

    async def drop_alias_target(self, alias: str) -> None:
        current = {a.alias_name: a.collection_name for a in (await self.q.get_aliases()).aliases}.get(alias)
        await self.q.update_collection_aliases(change_aliases_operations=[
            models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=alias))])
        if current:
            await self.q.delete_collection(current)
