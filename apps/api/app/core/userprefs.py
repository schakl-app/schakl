"""Per-user personal preferences (CLAUDE.md UX §6 — personal, in-view settings).

A small JSONB blob per (org, user), namespaced by feature (``time``, …). The web reads it in
layout loads and writes a single namespace at a time; the PUT shallow-merges so unrelated
namespaces are preserved. Any authenticated member manages only their own row.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.models import UserPref
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/prefs", tags=["prefs"])


class UserPrefs(BaseModel):
    prefs: dict[str, Any] = Field(default_factory=dict)


class UserPrefsUpdate(BaseModel):
    # Shallow-merged (per top-level namespace) into the stored blob.
    prefs: dict[str, Any] = Field(default_factory=dict)


async def _row(ctx: RequestContext) -> UserPref | None:
    stmt = select(UserPref).where(
        UserPref.org_id == ctx.org.id, UserPref.user_id == ctx.user.id
    )
    return await ctx.session.scalar(stmt)


@router.get("", response_model=UserPrefs)
async def get_prefs(ctx: RequestContext = Depends(require_context)) -> UserPrefs:
    row = await _row(ctx)
    return UserPrefs(prefs=dict(row.prefs) if row is not None else {})


@router.put("", response_model=UserPrefs)
async def set_prefs(
    payload: UserPrefsUpdate, ctx: RequestContext = Depends(require_context)
) -> UserPrefs:
    row = await _row(ctx)
    merged: dict[str, Any] = dict(row.prefs) if row is not None else {}
    for key, value in payload.prefs.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    if row is None:
        row = UserPref(org_id=ctx.org.id, user_id=ctx.user.id, prefs=merged)
        ctx.session.add(row)
    else:
        row.prefs = merged
    await ctx.session.flush()
    return UserPrefs(prefs=dict(row.prefs))
