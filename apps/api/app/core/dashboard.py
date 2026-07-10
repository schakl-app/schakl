"""My Day dashboard preferences (CLAUDE.md §10).

Every member arranges their own dashboard (which widgets, in which order); managers curate
an org-wide default template that applies to anyone without a personal layout. The API only
stores ordered widget keys — the web registry decides what each key renders.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.models import DashboardPref
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardPrefs(BaseModel):
    # None = no explicit layout at this level; the client falls back to all widgets.
    widgets: list[str] | None
    source: str  # "user" | "default" | "none"


class DashboardPrefsUpdate(BaseModel):
    widgets: list[str] = Field(max_length=50)


async def _row(
    ctx: RequestContext, user_id: uuid.UUID | None
) -> DashboardPref | None:
    stmt = select(DashboardPref).where(DashboardPref.org_id == ctx.org.id)
    stmt = stmt.where(
        DashboardPref.user_id == user_id if user_id else DashboardPref.user_id.is_(None)
    )
    return await ctx.session.scalar(stmt)


@router.get(
    "/prefs",
    response_model=DashboardPrefs,
    dependencies=[require_permission("dashboard.prefs.read")],
)
async def get_prefs(ctx: RequestContext = Depends(require_context)) -> DashboardPrefs:
    """The effective layout for the current user: own row → org template → none."""
    own = await _row(ctx, ctx.user.id)
    if own is not None:
        return DashboardPrefs(widgets=list(own.widgets), source="user")
    default = await _row(ctx, None)
    if default is not None:
        return DashboardPrefs(widgets=list(default.widgets), source="default")
    return DashboardPrefs(widgets=None, source="none")


async def _upsert(
    ctx: RequestContext, user_id: uuid.UUID | None, widgets: list[str]
) -> DashboardPref:
    row = await _row(ctx, user_id)
    if row is None:
        row = DashboardPref(org_id=ctx.org.id, user_id=user_id, widgets=widgets)
        ctx.session.add(row)
    else:
        row.widgets = widgets
    await ctx.session.flush()
    return row


@router.put(
    "/prefs",
    response_model=DashboardPrefs,
    dependencies=[require_permission("dashboard.prefs.write")],
)
async def set_prefs(
    payload: DashboardPrefsUpdate, ctx: RequestContext = Depends(require_context)
) -> DashboardPrefs:
    row = await _upsert(ctx, ctx.user.id, payload.widgets)
    return DashboardPrefs(widgets=list(row.widgets), source="user")


@router.delete(
    "/prefs",
    status_code=204,
    dependencies=[require_permission("dashboard.prefs.write")],
)
async def reset_prefs(ctx: RequestContext = Depends(require_context)) -> None:
    """Drop the personal layout; the user falls back to the org template."""
    row = await _row(ctx, ctx.user.id)
    if row is not None:
        await ctx.session.delete(row)
        await ctx.session.flush()


@router.put(
    "/prefs/default",
    response_model=DashboardPrefs,
    dependencies=[require_permission("settings.dashboard.manage")],
)
async def set_default_prefs(
    payload: DashboardPrefsUpdate, ctx: RequestContext = Depends(require_context)
) -> DashboardPrefs:
    """The org-wide template that members inherit (``settings.dashboard.manage``)."""
    row = await _upsert(ctx, None, payload.widgets)
    return DashboardPrefs(widgets=list(row.widgets), source="default")
