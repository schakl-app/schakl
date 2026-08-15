"""REST endpoints for wordpress under ``/api/v1/wordpress`` (docs/WORDPRESS.md, §6, §9).

Deny-by-default: every route declares a permission (§15). The split that matters is that
``site.manage`` gates the credential and ``site.read`` gates the *facts about* it — an agency
can let every account manager see that a client's site is connected and has Rank Math, without
letting anyone rotate a WordPress administrator password.

``/brands`` is the one read that dials out, and it declares ``site.read`` rather than
``site.manage``: choosing which Rank Math brand to attach to a client is marketing work, not
credential work. It stays out of ``marketing`` because the call needs this module's own client
and §6 forbids reaching across for it — marketing asks through ``app/core/wordpress.py``.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.entitlements import license_write_gate
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.wordpress.schemas import (
    WordPressBrand,
    WordPressSiteCreate,
    WordPressSiteRead,
    WordPressSiteUpdate,
    WordPressVerifyResult,
)
from app.integrations.wordpress.service import WordPressService

# The licence gate is mounted here rather than per route: past expiry + grace the module goes
# read-only, so the panel keeps showing what was last observed while connecting, rotating and
# disconnecting turn 402 (epic #140). `license_write_gate` reads the method, so every GET below
# — including `/brands`, which is a read that happens to travel — survives an expired licence.
router = APIRouter(
    prefix="/wordpress",
    tags=["wordpress"],
    dependencies=[license_write_gate("wordpress")],
)


@router.get(
    "/sites",
    response_model=list[WordPressSiteRead],
    dependencies=[require_permission("wordpress.site.read")],
)
async def list_sites(
    website_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[WordPressSiteRead]:
    return await WordPressService(ctx).list(website_id=website_id)


@router.post(
    "/sites",
    response_model=WordPressSiteRead,
    status_code=201,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def connect_site(
    payload: WordPressSiteCreate,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead:
    return await WordPressService(ctx).create(payload)


@router.get(
    "/sites/by-website/{website_id}",
    response_model=WordPressSiteRead | None,
    dependencies=[require_permission("wordpress.site.read")],
)
async def site_for_website(
    website_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead | None:
    """The one credential a website has, or ``null``.

    Literal segment, so declared before ``/sites/{site_id}``. ``null`` rather than a 404
    because most websites have no WordPress connected and that is the panel's ordinary empty
    state, not an error worth logging once per page view.
    """
    return await WordPressService(ctx).for_website(website_id)


@router.get(
    "/sites/{site_id}",
    response_model=WordPressSiteRead,
    dependencies=[require_permission("wordpress.site.read")],
)
async def get_site(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead:
    return await WordPressService(ctx).get(site_id)


@router.patch(
    "/sites/{site_id}",
    response_model=WordPressSiteRead,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def update_site(
    site_id: uuid.UUID,
    payload: WordPressSiteUpdate,
    ctx: RequestContext = Depends(require_context),
) -> WordPressSiteRead:
    return await WordPressService(ctx).update(site_id, payload)


@router.delete(
    "/sites/{site_id}",
    status_code=204,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def disconnect_site(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await WordPressService(ctx).delete(site_id)


@router.post(
    "/sites/{site_id}/verify",
    response_model=WordPressVerifyResult,
    dependencies=[require_permission("wordpress.site.manage")],
)
async def verify_site(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> WordPressVerifyResult:
    """Probe the site and store what was observed.

    Answers 200 for a credential that was refused: the per-capability answer *is* the response,
    and an exception is the one shape that cannot carry it. ``ok`` says whether anything got
    through at all.
    """
    return await WordPressService(ctx).verify(site_id)


@router.get(
    "/sites/{site_id}/brands",
    response_model=list[WordPressBrand],
    dependencies=[require_permission("wordpress.site.read")],
)
async def list_brands(
    site_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[WordPressBrand]:
    """The Rank Math brands this site tracks — the marketing link picker's options."""
    return await WordPressService(ctx).brands(site_id)
