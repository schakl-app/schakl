"""Sidebar navigation preferences (#169) — DashboardPref's resolution, one problem over.

Every member arranges their own sidebar (order + hidden module items); admins curate an
org-wide default that applies to anyone without a personal layout (Instellingen →
Navigatie). The API stores ordered ``{key, hidden}`` entries — the web registry decides what
each key renders, so a module enabled later simply falls back to its declared position and
always appears.

Admins may additionally give a nav item or a nav **group** a tenant-chosen **label**
(``{nl, en}``) — the org's own words for "Klanten" or "Hosting & domeinen". These labels are
org-wide config, so they only live on the org-default row (``PUT /nav/prefs/default``); a
personal row carries order/visibility only. Resolving a personal layout therefore takes its
order/visibility from the user's row but its item/group labels from the org default, merged
by key. The default row is stored as ``{"items": [...], "groups": [...]}``; a legacy plain
list is read tolerantly as items-only (no migration — the column is JSONB).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.core.models import NavPref
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/nav", tags=["nav"])

# A tenant label is per-locale text; we only accept the locales the app ships (§8). Anything
# else is a client bug, not tenant data, so it is rejected rather than silently stored.
_LABEL_LOCALES = ("nl", "en")
_LABEL_MAX = 60


def _clean_label(value: dict[str, str] | None) -> dict[str, str] | None:
    """Validate + normalise a tenant label dict: known locales only, stripped, empties dropped.

    Returns ``None`` when nothing survives, so "no custom label" is a single canonical shape
    (a falsey label) rather than ``{}`` vs ``None`` vs ``{"nl": ""}``.
    """
    if not value:
        return None
    if not isinstance(value, dict):
        raise ValueError("label must be an object of locale → text")
    cleaned: dict[str, str] = {}
    for locale, text in value.items():
        if locale not in _LABEL_LOCALES:
            raise ValueError(f"unsupported locale: {locale}")
        text = (text or "").strip()
        if len(text) > _LABEL_MAX:
            raise ValueError(f"label too long (max {_LABEL_MAX})")
        if text:
            cleaned[locale] = text
    return cleaned or None


class NavPrefItem(BaseModel):
    key: str = Field(min_length=1, max_length=100)
    hidden: bool = False
    # Tenant label (org default only); ``None`` means "use the declared/i18n label".
    label: dict[str, str] | None = None

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _clean_label(value)


class NavGroupPref(BaseModel):
    """A tenant label for a sidebar *group* heading (e.g. ``assets`` → "Hosting & domeinen")."""

    key: str = Field(min_length=1, max_length=100)
    label: dict[str, str] | None = None

    @field_validator("label")
    @classmethod
    def _validate_label(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return _clean_label(value)


class NavPrefs(BaseModel):
    # None = no explicit layout at this level; the client falls back to declared positions.
    items: list[NavPrefItem] | None
    # Group labels always ride from the org default (even for a user with a personal row).
    groups: list[NavGroupPref] | None = None
    source: str  # "user" | "default" | "none"


class NavPrefsUpdate(BaseModel):
    items: list[NavPrefItem] = Field(max_length=100)
    # Only meaningful on the org default; ignored on a personal PUT (renaming is org config).
    groups: list[NavGroupPref] = Field(default_factory=list, max_length=100)


async def _row(ctx: RequestContext, user_id: uuid.UUID | None) -> NavPref | None:
    stmt = select(NavPref).where(NavPref.org_id == ctx.org.id)
    stmt = stmt.where(NavPref.user_id == user_id if user_id else NavPref.user_id.is_(None))
    return await ctx.session.scalar(stmt)


def _parse(row: NavPref) -> tuple[list[NavPrefItem], list[NavGroupPref]]:
    """Read a stored row tolerantly: a legacy plain list is items-only (no groups/labels)."""
    raw = row.items
    if isinstance(raw, dict):
        raw_items = raw.get("items") or []
        raw_groups = raw.get("groups") or []
    else:
        raw_items = raw or []
        raw_groups = []
    items = [NavPrefItem.model_validate(item) for item in raw_items]
    groups = [NavGroupPref.model_validate(group) for group in raw_groups]
    return items, groups


def _resolve(own: NavPref | None, default: NavPref | None) -> NavPrefs:
    """The effective layout: own order/visibility, labels + groups from the org default."""
    default_items, default_groups = _parse(default) if default is not None else ([], [])
    label_by_key = {item.key: item.label for item in default_items if item.label}
    groups_out = default_groups or None
    if own is not None:
        own_items, _ = _parse(own)
        merged = [
            NavPrefItem(key=item.key, hidden=item.hidden, label=label_by_key.get(item.key))
            for item in own_items
        ]
        return NavPrefs(items=merged, groups=groups_out, source="user")
    if default is not None:
        return NavPrefs(items=default_items, groups=groups_out, source="default")
    return NavPrefs(items=None, groups=None, source="none")


@router.get(
    "/prefs",
    response_model=NavPrefs,
    dependencies=[require_permission("nav.prefs.read")],
)
async def get_prefs(ctx: RequestContext = Depends(require_context)) -> NavPrefs:
    """The effective layout for the current user: own row → org default → none."""
    own = await _row(ctx, ctx.user.id)
    default = await _row(ctx, None)
    return _resolve(own, default)


async def _upsert(
    ctx: RequestContext,
    user_id: uuid.UUID | None,
    payload: object,
) -> NavPref:
    row = await _row(ctx, user_id)
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
    """A member's personal order/visibility. Labels/groups are org config — stripped here."""
    stored = [{"key": item.key, "hidden": item.hidden} for item in payload.items]
    own = await _upsert(ctx, ctx.user.id, stored)
    default = await _row(ctx, None)
    return _resolve(own, default)


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
    """The org-wide default that members inherit, plus tenant item/group labels
    (``settings.nav.manage``)."""
    stored = {
        "items": [item.model_dump(exclude_none=True) for item in payload.items],
        # Only groups that actually carry a label are worth persisting.
        "groups": [group.model_dump(exclude_none=True) for group in payload.groups if group.label],
    }
    row = await _upsert(ctx, None, stored)
    items, groups = _parse(row)
    return NavPrefs(items=items, groups=groups or None, source="default")
