"""REST endpoints for hosting under ``/api/v1/hosting`` (issue #93, CLAUDE.md §6, §9).

Deny-by-default: every route declares a ``hosting.hosting.*`` permission (§15).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.hosting.schemas import HostingCreate, HostingRead, HostingUpdate
from app.modules.hosting.service import HostingService
from app.schemas import Page

router = APIRouter(prefix="/hosting", tags=["hosting"])


@router.get(
    "",
    response_model=Page[HostingRead],
    dependencies=[require_permission("hosting.hosting.read")],
)
async def list_hosting(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=200),
    sort: str | None = Query(
        None, description="name | ip_address | created_at | updated_at, '-' desc"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[HostingRead]:
    items, total = await HostingService(ctx).list(
        limit=limit, offset=offset, company_id=company_id, q=q, sort=sort
    )
    return Page(
        items=[HostingRead.model_validate(h) for h in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=HostingRead,
    status_code=201,
    dependencies=[require_permission("hosting.hosting.write")],
)
async def create_hosting(
    payload: HostingCreate,
    ctx: RequestContext = Depends(require_context),
) -> HostingRead:
    hosting = await HostingService(ctx).create(payload)
    return HostingRead.model_validate(hosting)


@router.get(
    "/{hosting_id}",
    response_model=HostingRead,
    dependencies=[require_permission("hosting.hosting.read")],
)
async def get_hosting(
    hosting_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> HostingRead:
    hosting = await HostingService(ctx).get(hosting_id)
    return HostingRead.model_validate(hosting)


@router.patch(
    "/{hosting_id}",
    response_model=HostingRead,
    dependencies=[require_permission("hosting.hosting.write")],
)
async def update_hosting(
    hosting_id: uuid.UUID,
    payload: HostingUpdate,
    ctx: RequestContext = Depends(require_context),
) -> HostingRead:
    hosting = await HostingService(ctx).update(hosting_id, payload)
    return HostingRead.model_validate(hosting)


@router.delete(
    "/{hosting_id}",
    status_code=204,
    dependencies=[require_permission("hosting.hosting.delete")],
)
async def delete_hosting(
    hosting_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await HostingService(ctx).delete(hosting_id)
