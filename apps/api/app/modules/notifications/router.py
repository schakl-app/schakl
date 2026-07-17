"""REST endpoints for notifications under ``/api/v1/notifications`` (CLAUDE.md §6, §9).

Every route serves the *calling* user's own rows: an inbox is personal, so there is no
``user_id`` parameter to get wrong. The only manager-gated surface is the org's default
preference matrix, which curates what a member inherits before they touch their own settings.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.notifications.channel_admin import ChannelService
from app.modules.notifications.defaults import ResolvedPref
from app.modules.notifications.prefs import (
    GeneralWrite,
    PrefWrite,
    effective_matrix,
    email_prefs_for_recipients,
    replace_overrides,
    save_email_pref,
)
from app.modules.notifications.schemas import (
    ActivityItem,
    ChannelCreate,
    ChannelRead,
    ChannelTestResult,
    ChannelUpdate,
    EmailPrefRead,
    EmailPrefWrite,
    EntityType,
    GeneralPreference,
    MarkAllResult,
    NotificationRead,
    PreferenceMatrix,
    PreferenceRow,
    PreferenceUpdate,
    ReadUpdate,
    UnreadCount,
    WatchRead,
    WatchUpdate,
)
from app.modules.notifications.service import NotificationService
from app.schemas import Page

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _matrix(resolved: dict[str, ResolvedPref]) -> PreferenceMatrix:
    """Every event, always — so the settings table renders complete and badges inheritance."""
    rows = [
        PreferenceRow(
            event_type=event_type,
            enabled=pref.enabled,
            delay_minutes=pref.delay_minutes,
            digest=pref.digest,
            digest_time=pref.digest_time,
            digest_weekday=pref.digest_weekday,
            source=pref.source,
        )
        for event_type, pref in resolved.items()
    ]
    any_pref = next(iter(resolved.values()))
    general = GeneralPreference(
        due_soon_days=any_pref.due_soon_days,
        quiet_hours_start=any_pref.quiet_hours_start,
        quiet_hours_end=any_pref.quiet_hours_end,
        source=any_pref.general_source,
    )
    return PreferenceMatrix(events=rows, general=general)


def _writes(payload: PreferenceUpdate) -> tuple[list[PrefWrite], GeneralWrite | None]:
    events = [
        PrefWrite(
            event_type=row.event_type,
            enabled=row.enabled,
            delay_minutes=row.delay_minutes,
            digest=row.digest,
            digest_time=row.digest_time,
            digest_weekday=row.digest_weekday,
        )
        for row in payload.events
    ]
    general = (
        GeneralWrite(
            due_soon_days=payload.general.due_soon_days,
            quiet_hours_start=payload.general.quiet_hours_start,
            quiet_hours_end=payload.general.quiet_hours_end,
        )
        if payload.general is not None
        else None
    )
    return events, general


# --- inbox ---------------------------------------------------------------------------- #
@router.get(
    "",
    response_model=Page[NotificationRead],
    dependencies=[require_permission("notifications.notification.read")],
)
async def list_notifications(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    unread: bool | None = Query(None, description="true = unread only, false = read only"),
    entity_type: EntityType | None = Query(None),
    sort: str | None = Query(None, description="created_at, '-' desc"),
    count: bool = Query(True, description="false skips the count query (docs/PERFORMANCE.md)"),
    ctx: RequestContext = Depends(require_context),
) -> Page[NotificationRead]:
    items, total = await NotificationService(ctx).list(
        limit=limit,
        offset=offset,
        unread=unread,
        entity_type=entity_type,
        sort=sort,
        count=count,
    )
    return Page(
        items=[NotificationRead.model_validate(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCount,
    dependencies=[require_permission("notifications.notification.read")],
)
async def unread_count(ctx: RequestContext = Depends(require_context)) -> UnreadCount:
    """The bell's badge. Comes from the API, never from counting the loaded page."""
    return UnreadCount(count=await NotificationService(ctx).unread_count())


@router.post(
    "/mark-all-read",
    response_model=MarkAllResult,
    dependencies=[require_permission("notifications.notification.write")],
)
async def mark_all_read(ctx: RequestContext = Depends(require_context)) -> MarkAllResult:
    return MarkAllResult(updated=await NotificationService(ctx).mark_all_read())


# Reading a record's notification-event feed requires being able to read that record (audit F7):
# notifications.notification.read is a blanket grant, so it can't stand in for per-entity access.
_ENTITY_READ_PERMISSION: dict[str, str] = {
    "task": "tasks.task.read",
    "project": "projects.project.read",
    "company": "companies.company.read",
    "leave_request": "leave.request.read",
    "timesheet": "time.entry.read",
}


# --- activity feed (per record; powers the panels other modules host) ------------------ #
@router.get(
    "/activity",
    response_model=list[ActivityItem],
    dependencies=[require_permission("notifications.notification.read")],
)
async def activity(
    entity_type: EntityType = Query(...),
    entity_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> list[ActivityItem]:
    entity_read = _ENTITY_READ_PERMISSION.get(entity_type)
    if entity_read is not None:
        ctx.require(entity_read)
    items = await NotificationService(ctx).activity(entity_type, entity_id, limit)
    return [ActivityItem.model_validate(item) for item in items]


# --- watch / mute a record ------------------------------------------------------------- #
@router.get(
    "/watch",
    response_model=WatchRead,
    dependencies=[require_permission("notifications.notification.read")],
)
async def get_watch(
    entity_type: EntityType = Query(...),
    entity_id: uuid.UUID = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> WatchRead:
    return WatchRead(watching=await NotificationService(ctx).watch_state(entity_type, entity_id))


@router.put(
    "/watch",
    response_model=WatchRead,
    dependencies=[require_permission("notifications.notification.write")],
)
async def set_watch(
    payload: WatchUpdate, ctx: RequestContext = Depends(require_context)
) -> WatchRead:
    watching = await NotificationService(ctx).set_watch(
        payload.entity_type, payload.entity_id, payload.watching
    )
    return WatchRead(watching=watching)


# --- preferences ------------------------------------------------------------------------ #
@router.get(
    "/preferences",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.notification.read")],
)
async def get_preferences(ctx: RequestContext = Depends(require_context)) -> PreferenceMatrix:
    """My effective matrix: what will actually happen, and which layer decided it."""
    return _matrix(await effective_matrix(ctx.session, ctx.org.id, ctx.user.id))


@router.put(
    "/preferences",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.notification.write")],
)
async def set_preferences(
    payload: PreferenceUpdate, ctx: RequestContext = Depends(require_context)
) -> PreferenceMatrix:
    events, general = _writes(payload)
    await replace_overrides(ctx.session, ctx.org.id, ctx.user.id, events, general)
    return _matrix(await effective_matrix(ctx.session, ctx.org.id, ctx.user.id))


@router.get(
    "/preferences/email",
    response_model=EmailPrefRead,
    dependencies=[require_permission("notifications.notification.read")],
)
async def get_email_preference(ctx: RequestContext = Depends(require_context)) -> EmailPrefRead:
    """My e-mail delivery rule (#17): off by default, one cadence for all my notifications."""
    pref = (await email_prefs_for_recipients(ctx.session, ctx.org.id, [ctx.user.id]))[ctx.user.id]
    return EmailPrefRead(
        enabled=pref.enabled,
        digest=pref.digest,  # type: ignore[arg-type]
        digest_time=pref.digest_time,
        digest_weekday=pref.digest_weekday,
        source=pref.source,
    )


@router.put(
    "/preferences/email",
    response_model=EmailPrefRead,
    dependencies=[require_permission("notifications.notification.write")],
)
async def set_email_preference(
    payload: EmailPrefWrite, ctx: RequestContext = Depends(require_context)
) -> EmailPrefRead:
    await save_email_pref(
        ctx.session,
        ctx.org.id,
        ctx.user.id,
        enabled=payload.enabled,
        digest=payload.digest,
        digest_time=payload.digest_time,
        digest_weekday=payload.digest_weekday,
    )
    return await get_email_preference(ctx)


@router.get(
    "/preferences/defaults",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.defaults.manage")],
)
async def get_default_preferences(
    ctx: RequestContext = Depends(require_context),
) -> PreferenceMatrix:
    """What a member inherits before they override anything (org-wide)."""
    return _matrix(await effective_matrix(ctx.session, ctx.org.id, None))


@router.put(
    "/preferences/defaults",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.defaults.manage")],
)
async def set_default_preferences(
    payload: PreferenceUpdate, ctx: RequestContext = Depends(require_context)
) -> PreferenceMatrix:
    events, general = _writes(payload)
    await replace_overrides(ctx.session, ctx.org.id, None, events, general)
    return _matrix(await effective_matrix(ctx.session, ctx.org.id, None))


# --- external channels (#17): admin-only, declared before ``/{notification_id}`` ---------- #
@router.get(
    "/channels",
    response_model=list[ChannelRead],
    dependencies=[require_permission("notifications.channels.manage")],
)
async def list_channels(
    ctx: RequestContext = Depends(require_context),
) -> list[ChannelRead]:
    return [ChannelRead(**c) for c in await ChannelService(ctx).list()]


@router.post(
    "/channels",
    response_model=ChannelRead,
    status_code=201,
    dependencies=[require_permission("notifications.channels.manage")],
)
async def create_channel(
    payload: ChannelCreate,
    ctx: RequestContext = Depends(require_context),
) -> ChannelRead:
    return ChannelRead(**await ChannelService(ctx).create(payload))


@router.patch(
    "/channels/{channel_id}",
    response_model=ChannelRead,
    dependencies=[require_permission("notifications.channels.manage")],
)
async def update_channel(
    channel_id: uuid.UUID,
    payload: ChannelUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ChannelRead:
    return ChannelRead(**await ChannelService(ctx).update(channel_id, payload))


@router.delete(
    "/channels/{channel_id}",
    status_code=204,
    dependencies=[require_permission("notifications.channels.manage")],
)
async def delete_channel(
    channel_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ChannelService(ctx).delete(channel_id)


@router.post(
    "/channels/{channel_id}/test",
    response_model=ChannelTestResult,
    dependencies=[require_permission("notifications.channels.manage")],
)
async def test_channel(
    channel_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ChannelTestResult:
    """Send a test message and report the provider's real error (#17)."""
    return await ChannelService(ctx).test(channel_id)


# --- single row (declared last: a static path must never be eaten by ``{id}``) ---------- #
@router.patch(
    "/{notification_id}",
    response_model=NotificationRead,
    dependencies=[require_permission("notifications.notification.write")],
)
async def set_read(
    notification_id: uuid.UUID,
    payload: ReadUpdate,
    ctx: RequestContext = Depends(require_context),
) -> NotificationRead:
    """Reversible: read and unread are the same non-destructive toggle (docs/UX.md)."""
    item = await NotificationService(ctx).set_read(notification_id, payload.read)
    return NotificationRead.model_validate(item)
