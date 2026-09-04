"""My Day dashboard preferences (CLAUDE.md §10).

Every member arranges their own dashboard (which widgets, in which column, in which order);
managers curate an org-wide default template that applies to anyone without a personal
layout. The API only stores widget keys — the web registry decides what each key renders.

The columns are stored rather than computed (#325). They used to be cut out of the flat list
at ``ceil(n/2)`` by the browser on every render, which made a tile's column a function of its
index: dragging one across only worked if it also crossed that index, and doing so pushed
whatever sat on the boundary the other way. Adding a widget re-cut the board too.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select

from app.core.models import DashboardPref
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


MAX_WIDGETS = 50
# The board is two columns (the web renders two flex stacks). Storing a third would be a
# layout no screen can show, and the tiles in it would silently stop being drawn.
MAX_COLUMNS = 2


class DashboardPrefs(BaseModel):
    # None = no explicit layout at this level; the client falls back to all widgets.
    widgets: list[str] | None
    # Which column each widget sits in (#325); ``widgets`` is this flattened. None = never
    # arranged in columns — the client splits the flat order the way it always has.
    columns: list[list[str]] | None = None
    source: str  # "user" | "default" | "none"


class DashboardPrefsUpdate(BaseModel):
    """A layout is the columns the board shows; the flat list is what they read as.

    Both fields are accepted, and that is the rolling-deploy contract (docs/WORKFLOW.md): the
    previous release posts ``widgets`` alone and must keep saving, and it also *reads* only
    ``widgets``, so the flat order has to stay written. When ``columns`` arrives it is the
    authority — ``widgets`` is derived from it rather than trusted alongside it, so the two
    stored shapes can never disagree about which widgets are on the board.
    """

    widgets: list[str] | None = Field(default=None, max_length=MAX_WIDGETS)
    columns: list[list[str]] | None = Field(default=None, max_length=MAX_COLUMNS)

    @model_validator(mode="after")
    def _resolve(self) -> DashboardPrefsUpdate:
        if self.columns is not None:
            flat = [key for column in self.columns for key in column]
            if len(flat) > MAX_WIDGETS:
                raise ValueError(f"at most {MAX_WIDGETS} widgets")
            self.widgets = flat
        elif self.widgets is None:
            raise ValueError("widgets or columns is required")
        return self

    @property
    def resolved_widgets(self) -> list[str]:
        return self.widgets or []


async def _row(ctx: RequestContext, user_id: uuid.UUID | None) -> DashboardPref | None:
    stmt = select(DashboardPref).where(DashboardPref.org_id == ctx.org.id)
    stmt = stmt.where(
        DashboardPref.user_id == user_id if user_id else DashboardPref.user_id.is_(None)
    )
    return await ctx.session.scalar(stmt)


def _read(row: DashboardPref, source: str) -> DashboardPrefs:
    return DashboardPrefs(
        widgets=list(row.widgets),
        columns=[list(column) for column in row.columns] if row.columns is not None else None,
        source=source,
    )


@router.get(
    "/prefs",
    response_model=DashboardPrefs,
    dependencies=[require_permission("dashboard.prefs.read")],
)
async def get_prefs(ctx: RequestContext = Depends(require_context)) -> DashboardPrefs:
    """The effective layout for the current user: own row → org template → none."""
    own = await _row(ctx, ctx.user.id)
    if own is not None:
        return _read(own, "user")
    # The org template is the *staff* board: Instellingen → Dashboard arranges the staff
    # gallery, and the portal gallery shares no key with it. Handing it to a client login
    # resolved to a layout in which every key was unknown — an empty homepage the moment an
    # agency curated its own — so an external login inherits nothing and opens on its whole
    # gallery, exactly as a staff member with no template does.
    if ctx.is_portal:
        return DashboardPrefs(widgets=None, columns=None, source="none")
    default = await _row(ctx, None)
    if default is not None:
        return _read(default, "default")
    return DashboardPrefs(widgets=None, columns=None, source="none")


async def _upsert(
    ctx: RequestContext, user_id: uuid.UUID | None, payload: DashboardPrefsUpdate
) -> DashboardPref:
    widgets = payload.resolved_widgets
    # An update that names no columns *clears* them rather than leaving the old ones: the
    # widgets it does name are the whole board, and a stale column list would still be holding
    # keys that are no longer on it. That is also what the org template posts (the settings
    # screen arranges one ordered list, not two columns), so an inheritor keeps the split.
    columns = payload.columns
    row = await _row(ctx, user_id)
    if row is None:
        row = DashboardPref(org_id=ctx.org.id, user_id=user_id, widgets=widgets, columns=columns)
        ctx.session.add(row)
    else:
        row.widgets = widgets
        row.columns = columns
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
    row = await _upsert(ctx, ctx.user.id, payload)
    return _read(row, "user")


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
    row = await _upsert(ctx, None, payload)
    return _read(row, "default")
