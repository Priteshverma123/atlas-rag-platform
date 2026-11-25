"""Tier 2, long-term user memory (facts, preferences, goals).

Design (mem0 2026 + Graphiti):
  * **write path is ADD-only**: one cheap structured extraction per turn, no UPDATE/DELETE
    decisions in the hot path (they are where memory systems hallucinate);
  * **consolidation** runs in the background: near-duplicates merge, contradicted facts are
    *invalidated* (``invalid_at`` + ``superseded_by``), never deleted, so history is auditable;
  * **provenance**: every fact links to the message it came from;
  * **user control**: facts are listable and deletable via ``/v1/memory`` (GDPR);
  * retrieval returns only currently-valid facts relevant to the query (top-k by similarity).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient, models
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from atlas.db.models import MemoryFact
from atlas.embeddings.client import Embedder
from atlas.llm.client import LLMClient

COLLECTION = "atlas_memory"


class FactCandidate(BaseModel):
    fact: str = Field(description="a self-contained statement about the user, e.g. 'Works in the EU finance team'")
    category: str = Field(description="one of: preference, fact, goal, context")
    confidence: float = Field(description="0-1 confidence that this is durable and true about the user")


class Extraction(BaseModel):
    facts: list[FactCandidate] = Field(description="durable facts about the USER only; empty if none")


EXTRACT_SYSTEM = """Extract durable facts about the USER from their message (role, team, projects, preferences,
goals, constraints). Ignore facts about documents or the world, one-off requests, and anything the assistant
said. Never extract secrets, credentials, health, or other sensitive personal data. Usually return an empty list."""


class SupersedeDecision(BaseModel):
    superseded_ids: list[str] = Field(description="ids of facts made obsolete or contradicted by a newer fact")


class FactStore:
    def __init__(self, qdrant: AsyncQdrantClient, embedder: Embedder, llm: LLMClient) -> None:
        self.q, self.embedder, self.llm = qdrant, embedder, llm
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
            await self.q.create_collection(COLLECTION, vectors_config=models.VectorParams(
                size=self.embedder.dim, distance=models.Distance.COSINE))
            for f in ("tenant_id", "user_id"):
                await self.q.create_payload_index(COLLECTION, f, field_schema=models.PayloadSchemaType.KEYWORD)
            await self.q.create_payload_index(COLLECTION, "valid", field_schema=models.PayloadSchemaType.BOOL)

    # ------------------------------------------------------------------ write path (ADD-only)
    async def extract_and_add(self, s: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID,
                              user_message: str, source_message_id: uuid.UUID, min_confidence: float = 0.6) -> int:
        if len(user_message.split()) < 4:
            return 0
        res, meta = await self.llm.structured("fast", [{"role": "system", "content": EXTRACT_SYSTEM},
                                                       {"role": "user", "content": user_message[:4000]}],
                                              Extraction, tenant_id=str(tenant_id), max_tokens=400)
        if meta.provider == "mock":
            return 0
        kept = [f for f in res.facts if f.confidence >= min_confidence and len(f.fact) > 5][:5]
        if not kept:
            return 0
        await self.ensure()
        vecs = await self.embedder.embed([f.fact for f in kept], kind="document")
        rows = [MemoryFact(id=uuid.uuid4(), tenant_id=tenant_id, user_id=user_id, fact=f.fact[:500],
                           category=f.category if f.category in ("preference", "fact", "goal", "context") else "fact",
                           confidence=f.confidence, source_message_id=source_message_id) for f in kept]
        s.add_all(rows)
        await self.q.upsert(COLLECTION, points=[models.PointStruct(
            id=str(r.id), vector=v, payload={"tenant_id": str(tenant_id), "user_id": str(user_id), "fact": r.fact,
                                             "category": r.category, "valid": True})
            for r, v in zip(rows, vecs, strict=True)])
        return len(rows)

    # ------------------------------------------------------------------ read path
    async def relevant(self, tenant_id: str, user_id: str, query: str, k: int = 5,
                       min_score: float = 0.35) -> list[str]:
        await self.ensure()
        vec = await self.embedder.embed_one(query, kind="query")
        res = await self.q.query_points(COLLECTION, query=vec, limit=k, score_threshold=min_score, with_payload=True,
                                        query_filter=models.Filter(must=[
                                            models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)),
                                            models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                                            models.FieldCondition(key="valid", match=models.MatchValue(value=True))]))
        return [str((p.payload or {}).get("fact", "")) for p in res.points]

    async def invalidate(self, s: AsyncSession, fact_ids: list[uuid.UUID], superseded_by: uuid.UUID | None) -> None:
        if not fact_ids:
            return
        await s.execute(update(MemoryFact).where(MemoryFact.id.in_(fact_ids), MemoryFact.invalid_at.is_(None))
                        .values(invalid_at=datetime.now(UTC), superseded_by=superseded_by))
        await self.ensure()
        await self.q.set_payload(COLLECTION, payload={"valid": False}, points=[str(i) for i in fact_ids])

    async def delete_for_user(self, s: AsyncSession, user_id: uuid.UUID, fact_id: uuid.UUID | None = None) -> int:
        """Hard delete on user request (right to erasure): DB rows and vectors."""
        q = select(MemoryFact.id).where(MemoryFact.user_id == user_id)
        if fact_id:
            q = q.where(MemoryFact.id == fact_id)
        ids = list((await s.execute(q)).scalars())
        if ids:
            for fid in ids:
                await s.delete(await s.get(MemoryFact, fid))
            await self.ensure()
            await self.q.delete(COLLECTION, points_selector=models.PointIdsList(points=[str(i) for i in ids]))
        return len(ids)

    # ------------------------------------------------------------------ consolidation (background)
    async def consolidate(self, s: AsyncSession, tenant_id: uuid.UUID, user_id: uuid.UUID,
                          dup_threshold: float = 0.92, related_threshold: float = 0.80) -> dict[str, int]:
        facts = list((await s.execute(select(MemoryFact).where(
            MemoryFact.user_id == user_id, MemoryFact.invalid_at.is_(None)).order_by(MemoryFact.created_at.desc())
        )).scalars())
        if len(facts) < 2:
            return {"duplicates": 0, "superseded": 0}
        vecs = await self.embedder.embed([f.fact for f in facts], kind="document")
        dup, sup = 0, 0
        dead: set[uuid.UUID] = set()
        for i, newer in enumerate(facts):  # newest first
            if newer.id in dead:
                continue
            related: list[MemoryFact] = []
            for j in range(i + 1, len(facts)):
                older = facts[j]
                if older.id in dead:
                    continue
                sim = sum(a * b for a, b in zip(vecs[i], vecs[j], strict=True))
                if sim >= dup_threshold:
                    await self.invalidate(s, [older.id], newer.id)
                    dead.add(older.id)
                    dup += 1
                elif sim >= related_threshold:
                    related.append(older)
            if related:
                listing = "\n".join(f"- id={r.id}: {r.fact}" for r in related)
                res, meta = await self.llm.structured("fast", [
                    {"role": "system", "content": "Given a NEW fact about a user and OLDER facts, list the ids of older "
                                                  "facts that the new fact contradicts or makes obsolete. Facts that "
                                                  "can both be true are not superseded."},
                    {"role": "user", "content": f"NEW: {newer.fact}\nOLDER:\n{listing}"}],
                    SupersedeDecision, tenant_id=str(tenant_id), max_tokens=200)
                if meta.provider != "mock":
                    valid = {str(r.id) for r in related}
                    ids = [uuid.UUID(x) for x in res.superseded_ids if x in valid]
                    await self.invalidate(s, ids, newer.id)
                    dead.update(ids)
                    sup += len(ids)
        return {"duplicates": dup, "superseded": sup}
