"""Sidebar navigation preferences (#169) — DashboardPref's resolution, one problem over.

Every member arranges their own sidebar (order + hidden module items); admins curate an
org-wide default that applies to anyone without a personal layout (Instellingen →
Navigatie). The API only stores ordered ``{key, hidden}`` entries — the web registry
decides what each key renders, so a module enabled later simply falls back to its declared
position and always appears.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.models import NavPref
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/nav", tags=["nav"])


class NavPrefItem(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    hidden: bool = False


class NavPrefs(BaseModel):
    # None = no explicit layout at this level; the client falls back to declared positions.
    items: list[NavPrefItem] | None
    source: str  # "user" | "default" | "none"


class NavPrefsUpdate(BaseModel):
    items: list[NavPrefItem] = Field(max_length=100)


async def _row(ctx: RequestContext, user_id: uuid.UUID | None) -> NavPref | None:
    stmt = select(NavPref).where(NavPref.org_id == ctx.org.id)
    stmt = stmt.where(NavPref.user_id == user_id if user_id else NavPref.user_id.is_(None))
    return await ctx.session.scalar(stmt)


def _read(row: NavPref) -> list[NavPrefItem]:
    return [NavPrefItem.model_validate(item) for item in row.items]


@router.get(
    "/prefs",
    response_model=NavPrefs,
    dependencies=[require_permission("nav.prefs.read")],
)
async def get_prefs(ctx: RequestContext = Depends(require_context)) -> NavPrefs:
    """The effective layout for the current user: own row → org default → none."""
    own = await _row(ctx, ctx.user.id)
    if own is not None:
        return NavPrefs(items=_read(own), source="user")
    default = await _row(ctx, None)
    if default is not None:
        return NavPrefs(items=_read(default), source="default")
    return NavPrefs(items=None, source="none")


async def _upsert(
    ctx: RequestContext, user_id: uuid.UUID | None, items: list[NavPrefItem]
) -> NavPref:
    row = await _row(ctx, user_id)
    payload = [item.model_dump() for item in items]
    if row is None:
        row = NavPref(org_id=ctx.org.id, user_id=user_id, items=payload)
        ctx.session.add(row)
    else:
        row.items = payload
    await ctx.session.flush()
    return row


@router.put(
    "/prefs",
    response_model=NavPrefs,
    dependencies=[require_permission("nav.prefs.write")],
)
async def set_prefs(
    payload: NavPrefsUpdate, ctx: RequestContext = Depends(require_context)
) -> NavPrefs:
    row = await _upsert(ctx, ctx.user.id, payload.items)
    return NavPrefs(items=_read(row), source="user")


@router.delete(
    "/prefs",
    status_code=204,
    dependencies=[require_permission("nav.prefs.write")],
)
async def reset_prefs(ctx: RequestContext = Depends(require_context)) -> None:
    """Drop the personal layout; the user falls back to the org default."""
    row = await _row(ctx, ctx.user.id)
    if row is not None:
        await ctx.session.delete(row)
        await ctx.session.flush()


@router.put(
    "/prefs/default",
    response_model=NavPrefs,
    dependencies=[require_permission("settings.nav.manage")],
)
async def set_default_prefs(
    payload: NavPrefsUpdate, ctx: RequestContext = Depends(require_context)
) -> NavPrefs:
    """The org-wide default that members inherit (``settings.nav.manage``)."""
    row = await _upsert(ctx, None, payload.items)
    return NavPrefs(items=_read(row), source="default")
