"""Knowledge Base API: collections, documents (file | text | URL), chunks, re-processing."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Request, UploadFile

from atlas.api.schemas import CollectionCreate, CollectionUpdate, TextDocument, UrlDocument
from atlas.api.security import ReadCtx, WriteCtx
from atlas.kb.service import KnowledgeBase, _coll

router = APIRouter(prefix="/v1", tags=["knowledge-base"])


def kb(request: Request) -> KnowledgeBase:
    return request.app.state.kb  # type: ignore[no-any-return]


@router.post("/collections", status_code=201)
async def create_collection(body: CollectionCreate, ctx: WriteCtx, request: Request) -> dict[str, Any]:
    return _coll(await kb(request).create_collection(ctx, body.name, body.description, body.chunking, body.retrieval))


@router.get("/collections")
async def list_collections(ctx: ReadCtx, request: Request) -> list[dict[str, Any]]:
    return await kb(request).list_collections(ctx)


@router.get("/collections/{cid}")
async def get_collection(cid: uuid.UUID, ctx: ReadCtx, request: Request) -> dict[str, Any]:
    return _coll(await kb(request).get_collection(ctx, cid))


@router.patch("/collections/{cid}")
async def update_collection(cid: uuid.UUID, body: CollectionUpdate, ctx: WriteCtx, request: Request) -> dict[str, Any]:
    return _coll(await kb(request).update_collection(ctx, cid, body.retrieval, body.chunking))


@router.delete("/collections/{cid}", status_code=204)
async def delete_collection(cid: uuid.UUID, ctx: WriteCtx, request: Request) -> None:
    await kb(request).delete_collection(ctx, cid)


@router.post("/collections/{cid}/documents", status_code=202)
async def upload_document(cid: uuid.UUID, ctx: WriteCtx, request: Request,
                          file: Annotated[UploadFile, File()],
                          acl_groups: Annotated[str | None, Form()] = None,
                          title: Annotated[str, Form()] = "") -> dict[str, Any]:
    data = await file.read()
    groups = [g.strip() for g in acl_groups.split(",") if g.strip()] if acl_groups else None
    doc, created = await kb(request).add_document(ctx, cid, data=data, filename=file.filename or "upload",
                                                  declared_mime=file.content_type, title=title, acl_groups=groups)
    return {"document_id": str(doc.id), "version": doc.version, "state": doc.state, "queued": created}


@router.post("/collections/{cid}/documents/batch", status_code=202)
async def upload_batch(cid: uuid.UUID, ctx: WriteCtx, request: Request,
                       files: Annotated[list[UploadFile], File(description="up to 500 files")],
                       acl_groups: Annotated[str | None, Form()] = None) -> dict[str, Any]:
    """Bulk ingestion: one request, many files. Each file becomes an independent, idempotent job on the
    ingest queue, so throughput scales with the number of ingest workers, not with this request."""
    if len(files) > 500:
        from atlas.common.errors import InvalidInput

        raise InvalidInput("max 500 files per batch request")
    groups = [g.strip() for g in acl_groups.split(",") if g.strip()] if acl_groups else None
    results = []
    for f in files:
        try:
            doc, created = await kb(request).add_document(ctx, cid, data=await f.read(), filename=f.filename or "upload",
                                                          declared_mime=f.content_type, acl_groups=groups)
            results.append({"filename": f.filename, "document_id": str(doc.id), "queued": created})
        except Exception as exc:  # one bad file must not fail the batch
            results.append({"filename": f.filename, "error": str(exc)[:300]})
    return {"accepted": sum("document_id" in r for r in results), "rejected": sum("error" in r for r in results),
            "results": results}


@router.get("/collections/{cid}/stats")
async def collection_stats(cid: uuid.UUID, ctx: ReadCtx, request: Request) -> dict[str, Any]:
    """Ingestion progress + throughput (used by the console and the ingest benchmark)."""
    from sqlalchemy import text

    from atlas.db.session import tenant_session

    async with tenant_session(ctx.tenant_id) as s:
        states = {r[0]: r[1] for r in (await s.execute(text(
            "SELECT state, count(*) FROM documents WHERE collection_id = :c GROUP BY state"), {"c": cid})).all()}
        t = (await s.execute(text("""
            SELECT count(*) AS ready,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM updated_at - created_at)) AS p50_s,
                   percentile_cont(0.95) WITHIN GROUP (ORDER BY EXTRACT(EPOCH FROM updated_at - created_at)) AS p95_s,
                   EXTRACT(EPOCH FROM max(updated_at) - min(created_at)) AS wall_s
            FROM documents WHERE collection_id = :c AND state = 'ready'"""), {"c": cid})).mappings().one()
        chunks = (await s.execute(text("SELECT count(*) FROM chunks WHERE collection_id = :c AND kind <> 'parent'"),
                                  {"c": cid})).scalar_one()
    ready, wall = int(t["ready"] or 0), float(t["wall_s"] or 0)
    return {"documents": sum(states.values()), "by_state": states, "chunks": chunks,
            "time_to_ready_s": {"p50": t["p50_s"], "p95": t["p95_s"]},
            "throughput_docs_per_min": round(60 * ready / wall, 1) if wall > 0 else None}


@router.post("/collections/{cid}/documents/text", status_code=202)
async def add_text(cid: uuid.UUID, body: TextDocument, ctx: WriteCtx, request: Request) -> dict[str, Any]:
    name = body.source_id or f"text:{uuid.uuid4()}"
    doc, created = await kb(request).add_document(ctx, cid, data=body.text.encode(), filename=f"{name}.md",
                                                  declared_mime="text/markdown", title=body.title,
                                                  acl_groups=body.acl_groups, metadata=body.metadata)
    return {"document_id": str(doc.id), "version": doc.version, "state": doc.state, "queued": created}


@router.post("/collections/{cid}/documents/url", status_code=202)
async def add_url(cid: uuid.UUID, body: UrlDocument, ctx: WriteCtx, request: Request) -> dict[str, Any]:
    doc, created = await kb(request).add_document(ctx, cid, data=None, filename=str(body.url), url=str(body.url),
                                                  acl_groups=body.acl_groups)
    return {"document_id": str(doc.id), "version": doc.version, "state": doc.state, "queued": created}


@router.get("/collections/{cid}/documents")
async def list_documents(cid: uuid.UUID, ctx: ReadCtx, request: Request, limit: int = 100, offset: int = 0
                         ) -> list[dict[str, Any]]:
    return await kb(request).list_documents(ctx, cid, min(limit, 500), offset)


@router.get("/documents/{did}")
async def get_document(did: uuid.UUID, ctx: ReadCtx, request: Request) -> dict[str, Any]:
    return await kb(request).get_document(ctx, did)


@router.get("/documents/{did}/chunks")
async def get_chunks(did: uuid.UUID, ctx: ReadCtx, request: Request, limit: int = 200) -> list[dict[str, Any]]:
    """Chunk inspection (RAGFlow-style): see exactly what the index contains for a document."""
    return await kb(request).list_chunks(ctx, did, min(limit, 1000))


@router.post("/documents/{did}/reprocess", status_code=202)
async def reprocess(did: uuid.UUID, ctx: WriteCtx, request: Request) -> dict[str, str]:
    await kb(request).reprocess(ctx, did)
    return {"status": "queued"}


@router.delete("/documents/{did}", status_code=204)
async def delete_document(did: uuid.UUID, ctx: WriteCtx, request: Request) -> None:
    await kb(request).delete_document(ctx, did)
