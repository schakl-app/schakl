"""REST endpoints for websites under ``/api/v1/websites`` (issue #94, CLAUDE.md §6, §9).

A website renders under its domain, so the domain page lists them via ``?domain_id=``.
Deny-by-default: every route declares a ``websites.website.*`` permission (§15).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.websites.schemas import WebsiteCreate, WebsiteRead, WebsiteUpdate
from app.modules.websites.service import WebsiteService
from app.schemas import Page

router = APIRouter(prefix="/websites", tags=["websites"])


@router.get(
    "",
    response_model=Page[WebsiteRead],
    dependencies=[require_permission("websites.website.read")],
)
async def list_websites(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    domain_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Page[WebsiteRead]:
    items, total = await WebsiteService(ctx).list(limit=limit, offset=offset, domain_id=domain_id)
    return Page(
        items=[WebsiteRead.model_validate(w) for w in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=WebsiteRead,
    status_code=201,
    dependencies=[require_permission("websites.website.write")],
)
async def create_website(
    payload: WebsiteCreate,
    ctx: RequestContext = Depends(require_context),
) -> WebsiteRead:
    website = await WebsiteService(ctx).create(payload)
    return WebsiteRead.model_validate(website)


@router.get(
    "/{website_id}",
    response_model=WebsiteRead,
    dependencies=[require_permission("websites.website.read")],
)
async def get_website(
    website_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WebsiteRead:
    website = await WebsiteService(ctx).get(website_id)
    return WebsiteRead.model_validate(website)


@router.patch(
    "/{website_id}",
    response_model=WebsiteRead,
    dependencies=[require_permission("websites.website.write")],
)
async def update_website(
    website_id: uuid.UUID,
    payload: WebsiteUpdate,
    ctx: RequestContext = Depends(require_context),
) -> WebsiteRead:
    website = await WebsiteService(ctx).update(website_id, payload)
    return WebsiteRead.model_validate(website)


@router.delete(
    "/{website_id}",
    status_code=204,
    dependencies=[require_permission("websites.website.delete")],
)
async def delete_website(
    website_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await WebsiteService(ctx).delete(website_id)
