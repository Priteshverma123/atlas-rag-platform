"""Ingestion pipeline: idempotent, resumable stages with content-hash caching.

    FETCH → PARSE → CHUNK → EMBED → INDEX → READY      (queue: ingest)
                                         └→ ENRICH (contextual retrieval + doc summary)  (queue: light)

* Each stage writes a ``document_stages`` row (status, attempts, timings, stats) → progress UI,
  per-stage SLOs, and resumability.
* PARSE output is cached in object storage under sha256(content_hash, parser, pipeline_version):
  the same file uploaded twice, or re-chunked with a new template, is never re-parsed.
* EMBED hits the Redis embedding cache for unchanged chunk texts, so a new version of a
  document only pays for the chunks that changed (reported as ``embed_cache_hits``).
* INDEX upserts the new version, then deletes stale versions: readers never see an empty doc.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import orjson
from sqlalchemy import delete, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from atlas.common.errors import InvalidInput, NotFound
from atlas.common.logging import get_logger
from atlas.common.net import safe_fetch
from atlas.common.telemetry import span
from atlas.common.text import sha256_bytes, sha256_text
from atlas.container import Container
from atlas.db.models import Chunk, Collection, Document, DocumentStage
from atlas.db.session import tenant_session
from atlas.index.qdrant_store import point_id
from atlas.ingestion.chunking import ChunkDraft, chunk_document, params_for
from atlas.ingestion.enrich import contextualize, summarize
from atlas.ingestion.parsers.base import Block, ParsedDocument
from atlas.ingestion.parsers.registry import parse, sniff_mime

log = get_logger(__name__)


def raw_key(tenant_id: Any, doc_id: Any, version: int) -> str:
    return f"raw/{tenant_id}/{doc_id}/{version}"


@asynccontextmanager
async def stage(tenant_id: str, doc: Document, name: str, stats: dict[str, Any], *,
                set_state: bool = True) -> AsyncIterator[None]:
    """Records a stage. ``set_state=False`` for post-READY background stages (enrichment): the document is
    already searchable and must keep showing READY; a failure there is recorded on the stage only."""
    started = datetime.now(UTC)
    async with tenant_session(tenant_id) as s:
        await s.execute(pg_insert(DocumentStage).values(
            document_id=doc.id, version=doc.version, stage=name, tenant_id=doc.tenant_id, status="running",
            attempts=1, started_at=started).on_conflict_do_update(
            index_elements=["document_id", "version", "stage"],
            set_={"status": "running", "attempts": DocumentStage.attempts + 1, "started_at": started, "error": None}))
        if set_state:
            await s.execute(update(Document).where(Document.id == doc.id).values(state=name))
    try:
        with span(f"ingest.{name}", **{"atlas.document_id": str(doc.id)}):
            yield
    except Exception as exc:
        async with tenant_session(tenant_id) as s:
            await s.execute(update(DocumentStage).where(
                DocumentStage.document_id == doc.id, DocumentStage.version == doc.version,
                DocumentStage.stage == name).values(status="failed", error=str(exc)[:2000],
                                                    finished_at=datetime.now(UTC)))
            if set_state:
                await s.execute(update(Document).where(Document.id == doc.id).values(
                    state="failed", error=f"{name}: {str(exc)[:1000]}"))
        raise
    async with tenant_session(tenant_id) as s:
        await s.execute(update(DocumentStage).where(
            DocumentStage.document_id == doc.id, DocumentStage.version == doc.version,
            DocumentStage.stage == name).values(status="done", finished_at=datetime.now(UTC), stats=stats))


def _parsed_to_json(p: ParsedDocument) -> bytes:
    return orjson.dumps(dataclasses.asdict(p))


def _parsed_from_json(raw: bytes) -> ParsedDocument:
    d = orjson.loads(raw)
    d["blocks"] = [Block(**{**b, "bbox": tuple(b["bbox"]) if b.get("bbox") else None}) for b in d["blocks"]]
    return ParsedDocument(**d)


async def _load(tenant_id: str, document_id: str) -> tuple[Document, Collection]:
    async with tenant_session(tenant_id) as s:
        row = (await s.execute(select(Document, Collection).join(Collection, Collection.id == Document.collection_id)
                               .where(Document.id == uuid.UUID(document_id)))).first()
    if row is None:
        raise NotFound(f"document {document_id}")
    return row[0], row[1]


async def ingest_document(c: Container, tenant_id: str, document_id: str) -> dict[str, Any]:
    s = c.settings
    doc, coll = await _load(tenant_id, document_id)
    stats: dict[str, Any] = {}

    # ---------------------------------------------------------------- FETCH
    fetch_stats: dict[str, Any] = {}
    async with stage(tenant_id, doc, "fetching", fetch_stats):
        key = raw_key(tenant_id, doc.id, doc.version)
        data = await c.objects.get(key)
        if data is None:
            if not doc.source_uri.startswith(("http://", "https://")):
                raise InvalidInput("raw content missing and source is not a URL")
            data, ctype, final_url = await safe_fetch(doc.source_uri)
            await c.objects.put(key, data, ctype or "application/octet-stream")
            fetch_stats["final_url"] = final_url
        mime = sniff_mime(data, doc.source_uri, doc.mime)
        content_hash = sha256_bytes(data)
        fetch_stats.update(bytes=len(data), mime=mime)

    # ---------------------------------------------------------------- PARSE (cached)
    parse_stats: dict[str, Any] = {}
    async with stage(tenant_id, doc, "parsing", parse_stats):
        parser = s.parser if mime == "application/pdf" else "native"
        cache_key = sha256_text(f"{content_hash}:{parser}:{s.pipeline_version}")
        parsed_key = f"parsed/{cache_key}.json"
        cached = await c.objects.get(parsed_key)
        if cached is not None:
            parsed = _parsed_from_json(cached)
            parse_stats["cache"] = "hit"
        else:
            loop = asyncio.get_running_loop()
            parsed = await asyncio.wait_for(
                loop.run_in_executor(_parse_pool(s.parse_workers), _parse_job, data, mime, doc.source_uri, parser),
                timeout=s.parse_timeout_s)
            await c.objects.put(parsed_key, _parsed_to_json(parsed), "application/json")
            parse_stats["cache"] = "miss"
        parse_stats.update(blocks=len(parsed.blocks), pages=parsed.page_count, parser=parsed.parser)

    # ---------------------------------------------------------------- CHUNK
    chunk_stats: dict[str, Any] = {}
    async with stage(tenant_id, doc, "chunking", chunk_stats):
        template, params = params_for(s.config_path("chunking.yaml"), mime, coll.chunking.get("template"))
        drafts = chunk_document(parsed, params)
        if not any(d.kind != "parent" for d in drafts):
            raise InvalidInput("document produced no text (scanned PDF? try ATLAS_PARSER=docling for OCR)")
        chunk_stats.update(template=template, chunks=sum(d.kind != "parent" for d in drafts),
                           parents=sum(d.kind == "parent" for d in drafts))

    title = doc.title or parsed.title
    leaves = [d for d in drafts if d.kind != "parent"]
    ids = {d.ordinal: uuid.UUID(point_id(doc.id, doc.version, d.ordinal)) for d in drafts}

    # ---------------------------------------------------------------- EMBED
    embed_stats: dict[str, Any] = {}
    async with stage(tenant_id, doc, "embedding", embed_stats):
        before = dict(c.embedder.stats)
        texts = [d.embed_text(title) for d in leaves]
        dense = await c.embedder.embed(texts, kind="document")
        sparse = [c.bm25.encode_document(t) for t in texts]
        embed_stats.update(vectors=len(dense), cache_hits=c.embedder.stats["hits"] - before["hits"],
                           model=c.embedder.model)

    # ---------------------------------------------------------------- INDEX
    index_stats: dict[str, Any] = {}
    async with stage(tenant_id, doc, "indexing", index_stats):
        alias = coll.qdrant_collection
        await c.store.ensure_collection(alias, coll.embed_dim)
        points = [(str(ids[d.ordinal]), dv, sv, _payload(doc, coll, d, ids, title))
                  for d, dv, sv in zip(leaves, dense, sparse, strict=True)]
        await c.store.upsert(alias, points, batch=s.index_batch_size)
        await c.store.delete_document(alias, str(doc.id), keep_version=doc.version)
        async with tenant_session(tenant_id) as sess:
            await sess.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            rows = [_chunk_values(doc, coll, d, ids) for d in drafts]
            for i in range(0, len(rows), 1000):  # one multi-row INSERT per 1000 chunks
                await sess.execute(insert(Chunk), rows[i:i + 1000])
            await sess.execute(update(Document).where(Document.id == doc.id).values(
                state="ready", error=None, title=title, page_count=parsed.page_count, content_hash=content_hash,
                mime=mime))
        index_stats.update(points=len(points))

    await _invalidate(c, tenant_id, [f"doc:{doc.id}"])
    stats.update(fetch=fetch_stats, parse=parse_stats, chunk=chunk_stats, embed=embed_stats, index=index_stats)
    log.info("document_ready", document_id=str(doc.id), **{k: v for k, v in chunk_stats.items()})
    return stats


async def _invalidate(c: Container, tenant_id: str, tags: list[str]) -> None:
    """Best-effort: cached answers grounded on a changed document also expire by TTL, so a cache outage
    must never fail ingestion."""
    try:
        await c.llm.invalidate_cache(tenant_id, tags)
        await c.answer_cache.invalidate_tags(tags)
    except Exception:
        log.warning("cache_invalidation_failed", tags=tags, exc_info=True)


def _payload(doc: Document, coll: Collection, d: ChunkDraft, ids: dict[int, uuid.UUID], title: str,
             context: str | None = None) -> dict[str, Any]:
    return {"tenant_id": str(doc.tenant_id), "collection_id": str(coll.id), "document_id": str(doc.id),
            "version": doc.version, "chunk_id": str(ids[d.ordinal]), "ordinal": d.ordinal, "kind": d.kind,
            "title": title, "section_path": d.section_path, "page_start": d.page_start, "page_end": d.page_end,
            "text": d.text, "context": context, "acl_groups": list(doc.acl_groups), "text_hash": d.text_hash,
            "parent_id": str(ids[d.parent_ordinal]) if d.parent_ordinal is not None else None,
            "source_uri": doc.source_uri}


def _chunk_values(doc: Document, coll: Collection, d: ChunkDraft, ids: dict[int, uuid.UUID]) -> dict[str, Any]:
    return {"id": ids[d.ordinal], "tenant_id": doc.tenant_id, "collection_id": coll.id, "document_id": doc.id,
            "version": doc.version, "ordinal": d.ordinal, "kind": d.kind, "text": d.text,
            "section_path": d.section_path, "page_start": d.page_start, "page_end": d.page_end,
            "bboxes": {"boxes": d.bboxes}, "text_hash": d.text_hash, "token_count": d.token_count,
            "parent_id": ids[d.parent_ordinal] if d.parent_ordinal is not None else None}


_POOL: ProcessPoolExecutor | None = None


def _parse_pool(workers: int) -> ProcessPoolExecutor:
    """CPU-bound parsing runs in worker processes so many documents parse truly in parallel."""
    global _POOL
    if _POOL is None:
        # "spawn", not the Linux default "fork": forking a process that already runs threads (OTel exporter,
        # Redis/HTTP pools) can deadlock the child on a lock held at fork time. Found under load testing.
        import multiprocessing as mp

        _POOL = ProcessPoolExecutor(max_workers=max(1, workers), mp_context=mp.get_context("spawn"))
    return _POOL


def _parse_job(data: bytes, mime: str, filename: str, parser: str) -> ParsedDocument:
    return parse(data, mime, filename, prefer=parser)


async def enrich_document(c: Container, tenant_id: str, document_id: str) -> dict[str, Any]:
    """Contextual enrichment + document summary. Runs after the doc is READY (already searchable)."""
    doc, coll = await _load(tenant_id, document_id)
    async with tenant_session(tenant_id) as s:
        chunks = list((await s.execute(select(Chunk).where(
            Chunk.document_id == doc.id, Chunk.version == doc.version, Chunk.kind != "parent")
            .order_by(Chunk.ordinal))).scalars())
    if not chunks:
        return {"enriched": 0}
    full_text = "\n\n".join(ch.text for ch in chunks)
    stats: dict[str, Any] = {}
    async with stage(tenant_id, doc, "enriching", stats, set_state=False):
        contexts = await contextualize(c.llm, tenant_id, doc.title, full_text,
                                       [(str(ch.id), ch.section_path, ch.text) for ch in chunks])
        summary = await summarize(c.llm, tenant_id, doc.title, full_text)
        changed = [ch for ch in chunks if str(ch.id) in contexts]
        if changed:
            drafts = [ChunkDraft(ch.ordinal, ch.kind, ch.text, ch.section_path, ch.page_start, ch.page_end,
                                 token_count=ch.token_count, text_hash=ch.text_hash) for ch in changed]
            texts = [d.embed_text(doc.title, contexts[str(ch.id)]) for d, ch in zip(drafts, changed, strict=True)]
            dense = await c.embedder.embed(texts, kind="document")
            ids = {ch.ordinal: ch.id for ch in chunks}
            points = []
            for d, ch, dv, t in zip(drafts, changed, dense, texts, strict=True):
                d.parent_ordinal = next((o for o, i in ids.items() if i == ch.parent_id), None)
                points.append((str(ch.id), dv, c.bm25.encode_document(t),
                               _payload(doc, coll, d, ids, doc.title, contexts[str(ch.id)])))
            await c.store.upsert(coll.qdrant_collection, points)
            async with tenant_session(tenant_id) as s:
                for ch in changed:
                    await s.execute(update(Chunk).where(Chunk.id == ch.id).values(context_text=contexts[str(ch.id)]))
        if summary is not None:
            text = f"{doc.title}\n{summary.summary}\nTopics: {', '.join(summary.topics)}"
            sid = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{doc.id}:summary"))
            dv = (await c.embedder.embed([text], kind="document"))[0]
            sdraft = ChunkDraft(-1, "summary", text, "", None, None)
            payload = _payload(doc, coll, sdraft, {-1: uuid.UUID(sid)}, doc.title)
            await c.store.upsert(coll.qdrant_collection, [(sid, dv, c.bm25.encode_document(text), payload)])
            async with tenant_session(tenant_id) as s:
                await s.execute(update(Document).where(Document.id == doc.id).values(summary=summary.summary))
        stats.update(enriched=len(changed), summary=summary is not None)
    return stats
