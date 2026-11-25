"""Knowledge-base domain service: collections, documents, chunks (business rules live here;
API routers stay thin)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from atlas.common.errors import Conflict, InvalidInput, NotFound
from atlas.common.text import sha256_bytes
from atlas.container import Container
from atlas.db.models import Chunk, Collection, Document, DocumentStage
from atlas.db.session import tenant_session
from atlas.index.qdrant_store import QdrantStore
from atlas.ingestion.parsers.registry import sniff_mime
from atlas.ingestion.pipeline import raw_key
from atlas.kb.context import RequestContext


class KnowledgeBase:
    def __init__(self, c: Container, enqueue: Any) -> None:
        self.c, self.enqueue = c, enqueue

    # ------------------------------------------------------------------ collections
    async def create_collection(self, ctx: RequestContext, name: str, description: str = "",
                                chunking: dict[str, Any] | None = None, retrieval: dict[str, Any] | None = None
                                ) -> Collection:
        cid = uuid.uuid4()
        coll = Collection(id=cid, tenant_id=uuid.UUID(ctx.tenant_id), name=name, description=description,
                          embedding_model=self.c.settings.embed_model, embed_dim=self.c.settings.embed_dim,
                          chunking=chunking or {"template": "auto"}, retrieval=retrieval or {},
                          qdrant_collection=QdrantStore.alias_for(cid))
        try:
            async with tenant_session(ctx.tenant_id) as s:
                s.add(coll)
        except IntegrityError as exc:
            raise Conflict(f"collection '{name}' already exists") from exc
        await self.c.store.ensure_collection(coll.qdrant_collection, coll.embed_dim)
        return coll

    async def list_collections(self, ctx: RequestContext) -> list[dict[str, Any]]:
        async with tenant_session(ctx.tenant_id) as s:
            rows = (await s.execute(
                select(Collection, func.count(Document.id)).outerjoin(Document, Document.collection_id == Collection.id)
                .group_by(Collection.id).order_by(Collection.created_at))).all()
        return [{**_coll(c), "documents": n} for c, n in rows]

    async def get_collection(self, ctx: RequestContext, cid: uuid.UUID) -> Collection:
        async with tenant_session(ctx.tenant_id) as s:
            coll = await s.get(Collection, cid)
        if coll is None:
            raise NotFound("collection not found")
        return coll

    async def update_collection(self, ctx: RequestContext, cid: uuid.UUID, retrieval: dict[str, Any] | None,
                                chunking: dict[str, Any] | None) -> Collection:
        async with tenant_session(ctx.tenant_id) as s:
            coll = await s.get(Collection, cid)
            if coll is None:
                raise NotFound("collection not found")
            if retrieval is not None:
                coll.retrieval = retrieval
            if chunking is not None:
                coll.chunking = chunking
        return coll

    async def delete_collection(self, ctx: RequestContext, cid: uuid.UUID) -> None:
        coll = await self.get_collection(ctx, cid)
        async with tenant_session(ctx.tenant_id) as s:
            doc_ids = list((await s.execute(select(Document.id).where(Document.collection_id == cid))).scalars())
            await s.delete(await s.get(Collection, cid))  # cascades to documents, chunks, stages
        await self.c.store.drop_alias_target(coll.qdrant_collection)
        for did in doc_ids:
            await self.c.objects.delete_prefix(f"raw/{ctx.tenant_id}/{did}/")
        from atlas.ingestion.pipeline import _invalidate

        await _invalidate(self.c, ctx.tenant_id, [f"doc:{d}" for d in doc_ids])

    # ------------------------------------------------------------------ documents
    async def add_document(self, ctx: RequestContext, cid: uuid.UUID, *, data: bytes | None, filename: str,
                           url: str | None = None, declared_mime: str | None = None, title: str = "",
                           acl_groups: list[str] | None = None, metadata: dict[str, Any] | None = None
                           ) -> tuple[Document, bool]:
        """Returns (document, created_new_version). Re-uploading identical content is a no-op."""
        if data is None and not url:
            raise InvalidInput("provide a file, text or url")
        if data is not None and len(data) > self.c.settings.max_upload_mb * 1024 * 1024:
            raise InvalidInput(f"file exceeds {self.c.settings.max_upload_mb} MB")
        await self.get_collection(ctx, cid)
        source = url or filename
        mime = sniff_mime(data, filename, declared_mime) if data is not None else "text/html"
        content_hash = sha256_bytes(data) if data is not None else ""
        async with tenant_session(ctx.tenant_id) as s:
            doc = (await s.execute(select(Document).where(Document.collection_id == cid,
                                                          Document.source_uri == source))).scalar_one_or_none()
            if doc is not None and data is not None and doc.content_hash == content_hash and doc.state == "ready":
                return doc, False
            if doc is None:
                doc = Document(id=uuid.uuid4(), tenant_id=uuid.UUID(ctx.tenant_id), collection_id=cid,
                               source_uri=source, mime=mime, content_hash=content_hash, version=1,
                               title=title, state="received", acl_groups=acl_groups or [], doc_metadata=metadata or {},
                               size_bytes=len(data or b""))
                s.add(doc)
            else:
                doc.version += 1
                doc.content_hash, doc.mime, doc.state, doc.error = content_hash, mime, "received", None
                doc.size_bytes = len(data or b"")
                if acl_groups is not None:
                    doc.acl_groups = acl_groups
            await s.flush()
        if data is not None:
            await self.c.objects.put(raw_key(ctx.tenant_id, doc.id, doc.version), data, mime)
        await self.enqueue("ingest_document", ctx.tenant_id, str(doc.id), _queue_name="atlas:ingest",
                           _job_id=f"ingest:{doc.id}:{doc.version}")  # job id = idempotency key
        return doc, True

    async def get_document(self, ctx: RequestContext, did: uuid.UUID) -> dict[str, Any]:
        async with tenant_session(ctx.tenant_id) as s:
            doc = await s.get(Document, did)
            if doc is None:
                raise NotFound("document not found")
            stages = (await s.execute(select(DocumentStage).where(DocumentStage.document_id == did,
                                                                  DocumentStage.version == doc.version)
                                      .order_by(DocumentStage.started_at))).scalars().all()
            n_chunks = (await s.execute(select(func.count()).select_from(Chunk).where(
                Chunk.document_id == did, Chunk.kind != "parent"))).scalar_one()
        return {**_doc(doc), "chunks": n_chunks, "stages": [
            {"stage": st.stage, "status": st.status, "attempts": st.attempts, "error": st.error, "stats": st.stats,
             "duration_ms": (st.finished_at - st.started_at).total_seconds() * 1000
             if st.finished_at and st.started_at else None} for st in stages]}

    async def list_documents(self, ctx: RequestContext, cid: uuid.UUID, limit: int = 100, offset: int = 0
                             ) -> list[dict[str, Any]]:
        async with tenant_session(ctx.tenant_id) as s:
            docs = (await s.execute(select(Document).where(Document.collection_id == cid)
                                    .order_by(Document.created_at.desc()).limit(limit).offset(offset))).scalars()
            return [_doc(d) for d in docs]

    async def delete_document(self, ctx: RequestContext, did: uuid.UUID) -> None:
        async with tenant_session(ctx.tenant_id) as s:
            doc = await s.get(Document, did)
            if doc is None:
                raise NotFound("document not found")
            coll = await s.get(Collection, doc.collection_id)
            await s.delete(doc)  # cascades to chunks and stages
        if coll:
            await self.c.store.delete_document(coll.qdrant_collection, str(did))
        await self.c.objects.delete_prefix(f"raw/{ctx.tenant_id}/{did}/")
        from atlas.ingestion.pipeline import _invalidate

        await _invalidate(self.c, ctx.tenant_id, [f"doc:{did}"])

    async def reprocess(self, ctx: RequestContext, did: uuid.UUID) -> None:
        async with tenant_session(ctx.tenant_id) as s:
            doc = await s.get(Document, did)
            if doc is None:
                raise NotFound("document not found")
        await self.enqueue("ingest_document", ctx.tenant_id, str(did), _queue_name="atlas:ingest")

    async def list_chunks(self, ctx: RequestContext, did: uuid.UUID, limit: int = 200) -> list[dict[str, Any]]:
        async with tenant_session(ctx.tenant_id) as s:
            doc = await s.get(Document, did)
            if doc is None:
                raise NotFound("document not found")
            rows = (await s.execute(select(Chunk).where(Chunk.document_id == did, Chunk.version == doc.version)
                                    .order_by(Chunk.ordinal).limit(limit))).scalars()
            return [{"id": str(ch.id), "ordinal": ch.ordinal, "kind": ch.kind, "section_path": ch.section_path,
                     "page_start": ch.page_start, "page_end": ch.page_end, "tokens": ch.token_count,
                     "parent_id": str(ch.parent_id) if ch.parent_id else None, "context": ch.context_text,
                     "text": ch.text, "bboxes": ch.bboxes.get("boxes", [])} for ch in rows]


def _coll(c: Collection) -> dict[str, Any]:
    return {"id": str(c.id), "name": c.name, "description": c.description, "embedding_model": c.embedding_model,
            "chunking": c.chunking, "retrieval": c.retrieval, "created_at": c.created_at.isoformat()
            if c.created_at else None}


def _doc(d: Document) -> dict[str, Any]:
    return {"id": str(d.id), "collection_id": str(d.collection_id), "title": d.title, "source_uri": d.source_uri,
            "mime": d.mime, "version": d.version, "state": d.state, "error": d.error, "acl_groups": d.acl_groups,
            "page_count": d.page_count, "size_bytes": d.size_bytes, "summary": d.summary,
            "updated_at": d.updated_at.isoformat() if d.updated_at else None}
