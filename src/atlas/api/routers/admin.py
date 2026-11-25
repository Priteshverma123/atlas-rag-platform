"""Tenant bootstrap and API-key management.

Bootstrap requires the deployment's ``ATLAS_BOOTSTRAP_TOKEN`` (no default admin/admin login).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from atlas.api.schemas import BootstrapRequest, KeyCreate
from atlas.api.security import AdminCtx
from atlas.common.errors import Conflict, Unauthorized
from atlas.common.settings import get_settings
from atlas.db.apikeys import constant_time_equals, create_key
from atlas.db.models import ApiKey, Tenant
from atlas.db.session import admin_session

router = APIRouter(prefix="/v1/admin", tags=["admin"])


@router.post("/bootstrap", status_code=201)
async def bootstrap(body: BootstrapRequest, x_bootstrap_token: Annotated[str, Header()]) -> dict[str, Any]:
    """Creates a tenant and its first admin key. Returns the key ONCE."""
    if not constant_time_equals(x_bootstrap_token, get_settings().bootstrap_token.get_secret_value()):
        raise Unauthorized("bad bootstrap token")
    tid = uuid.uuid4()
    try:
        async with admin_session() as s:
            s.add(Tenant(id=tid, name=body.tenant_name, monthly_budget_usd=body.monthly_budget_usd))
    except IntegrityError as exc:
        raise Conflict("tenant exists") from exc
    raw, row = await create_key(tid, "bootstrap-admin", ["admin", "read", "write", "llm", "impersonate"])
    return {"tenant_id": str(tid), "api_key": raw, "key_id": str(row.id)}


@router.post("/keys", status_code=201)
async def new_key(body: KeyCreate, ctx: AdminCtx) -> dict[str, Any]:
    raw, row = await create_key(uuid.UUID(ctx.tenant_id), body.name, list(body.scopes), rpm=body.rpm, tpm=body.tpm,
                                budget_usd_day=body.budget_usd_day, user_external_id=body.user_external_id)
    return {"api_key": raw, "key_id": str(row.id), "scopes": row.scopes}


@router.get("/keys")
async def list_keys(ctx: AdminCtx) -> list[dict[str, Any]]:
    async with admin_session() as s:
        rows = (await s.execute(select(ApiKey).where(ApiKey.tenant_id == uuid.UUID(ctx.tenant_id)))).scalars()
        return [{"key_id": str(k.id), "name": k.name, "prefix": k.key_prefix, "scopes": k.scopes, "rpm": k.rpm,
                 "tpm": k.tpm, "budget_usd_day": k.budget_usd_day, "revoked": k.revoked_at is not None}
                for k in rows]


@router.delete("/keys/{key_id}", status_code=204)
async def revoke_key(key_id: uuid.UUID, ctx: AdminCtx) -> None:
    from sqlalchemy import func

    async with admin_session() as s:
        await s.execute(update(ApiKey).where(ApiKey.id == key_id, ApiKey.tenant_id == uuid.UUID(ctx.tenant_id))
                        .values(revoked_at=func.now()))
