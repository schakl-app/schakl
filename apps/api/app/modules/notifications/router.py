"""REST endpoints for notifications under ``/api/v1/notifications`` (CLAUDE.md §6, §9).

Every route serves the *calling* user's own rows: an inbox is personal, so there is no
``user_id`` parameter to get wrong. The only manager-gated surface is the org's default
preference matrix, which curates what a member inherits before they touch their own settings.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, Query

from app.config import settings
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.i18n import translate
from app.modules.notifications.channel_admin import ChannelService
from app.modules.notifications.defaults import ResolvedPref
from app.modules.notifications.prefs import (
    ChannelWrite,
    EmailWrite,
    GeneralWrite,
    PrefWrite,
    effective_channel_matrix,
    effective_email_matrix,
    effective_matrix,
    effective_web_push_matrix,
    replace_overrides,
    scope_channels,
)
from app.modules.notifications.prefs import (
    EmailScheduleWrite as EmailScheduleData,
)
from app.modules.notifications.schemas import (
    ActivityItem,
    ChannelCreate,
    ChannelPreference,
    ChannelPreferenceEvent,
    ChannelRead,
    ChannelTestResult,
    ChannelUpdate,
    EmailSchedule,
    EntityType,
    GeneralPreference,
    MarkAllResult,
    NotificationRead,
    PreferenceMatrix,
    PreferenceRow,
    PreferenceUpdate,
    PushConfig,
    PushSubscriptionCreate,
    PushSubscriptionRead,
    PushTestResult,
    PushUnsubscribe,
    ReadUpdate,
    UnreadCount,
    WatchRead,
    WatchUpdate,
)
from app.modules.notifications.service import NotificationService
from app.modules.notifications.webpush import PushSubscriptionService, vapid_keys
from app.schemas import Page

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _matrix(
    in_app: dict[str, ResolvedPref],
    email: dict[str, ResolvedPref],
    schedule: object,
    push: dict[str, ResolvedPref],
    push_schedule: object,
    configs: Sequence[object] = (),
    channel_events: dict[uuid.UUID, dict[str, ResolvedPref]] | None = None,
) -> PreferenceMatrix:
    """Every event, always — so the settings table renders complete and badges inheritance.

    Each row carries all three implicit channels' resolved rules and their independent inheritance
    sources; ``schedule`` / ``push_schedule`` are the scope's global digest schedules for e-mail
    and browser push (``prefs.EmailSchedule`` each). ``configs`` are this scope's external channels
    (#283, #295) — each becomes one more column, with its own per-event cadence and schedule.
    """
    rows = [
        PreferenceRow(
            event_type=event_type,
            enabled=pref.enabled,
            delay_minutes=pref.delay_minutes,
            digest=pref.digest,
            digest_time=pref.digest_time,
            digest_weekday=pref.digest_weekday,
            source=pref.source,
            email_enabled=email[event_type].enabled,
            email_delay_minutes=email[event_type].delay_minutes,
            email_digest=email[event_type].digest,
            email_source=email[event_type].source,
            push_enabled=push[event_type].enabled,
            push_delay_minutes=push[event_type].delay_minutes,
            push_digest=push[event_type].digest,
            push_source=push[event_type].source,
        )
        for event_type, pref in in_app.items()
    ]
    any_pref = next(iter(in_app.values()))
    general = GeneralPreference(
        due_soon_days=any_pref.due_soon_days,
        quiet_hours_start=any_pref.quiet_hours_start,
        quiet_hours_end=any_pref.quiet_hours_end,
        source=any_pref.general_source,
    )
    resolved = channel_events or {}
    return PreferenceMatrix(
        events=rows,
        general=general,
        email=EmailSchedule(
            digest_time=schedule.digest_time,
            digest_weekday=schedule.digest_weekday,
            source=schedule.source,
        ),
        push=EmailSchedule(
            digest_time=push_schedule.digest_time,
            digest_weekday=push_schedule.digest_weekday,
            source=push_schedule.source,
        ),
        channels=[
            ChannelPreference(
                id=config.id,
                name=config.name,
                kind=config.kind,
                digest_time=config.digest_time,
                digest_weekday=config.digest_weekday,
                events=[
                    ChannelPreferenceEvent(
                        event_type=event_type,
                        enabled=pref.enabled,
                        delay_minutes=pref.delay_minutes,
                        digest=pref.digest,
                    )
                    for event_type, pref in resolved.get(config.id, {}).items()
                ],
            )
            for config in configs
        ],
    )


def _manages_channels(ctx: RequestContext, user_id: uuid.UUID | None) -> bool:
    """May this caller configure the channels of this scope? (#295)

    One predicate for *seeing* a channel's column and for *writing* it, deliberately, because the
    channel blocks are wholesale: a caller shown no columns posts an empty list, and if that were
    still allowed to write it would clear every route the scope had. Reading and writing must
    therefore agree, or saving an unrelated in-app default silently un-routes `#crm`.

    Configuring a shared room is an administrative act (the same reason ``channels.manage`` gates
    connecting one), so on the org scope it takes that key **on top of** ``defaults.manage``. Both
    are admin-only by default, so this refuses nobody who could route a room before.
    """
    return ctx.can(
        "notifications.channels.manage"
        if user_id is None
        else "notifications.channels.manage_own"
    )


async def _load_matrix(  # noqa: ANN001
    session, org_id, user_id, *, include_channels: bool = True
) -> PreferenceMatrix:
    """Resolve every channel's matrix for one scope, then compose.

    Five queries flat, whatever the number of channels: the in-app rows, the e-mail rows, the
    web-push rows, the scope's channel configs, and every per-channel preference in one go
    (docs/PERFORMANCE.md — never one query per channel). ``include_channels=False`` skips the
    last two entirely — a caller who may not configure them has no use for the answer.
    """
    in_app = await effective_matrix(session, org_id, user_id)
    email, schedule = await effective_email_matrix(session, org_id, user_id)
    push, push_schedule = await effective_web_push_matrix(session, org_id, user_id)
    configs = await scope_channels(session, org_id, user_id) if include_channels else []
    channel_events = await effective_channel_matrix(session, org_id, configs)
    return _matrix(in_app, email, schedule, push, push_schedule, configs, channel_events)


async def _channel_writes(
    ctx: RequestContext, payload: PreferenceUpdate, user_id: uuid.UUID | None
) -> list[ChannelWrite] | None:
    """Flatten the per-channel blocks, refusing any channel outside the scope being written.

    ``None`` means *leave this scope's channel rows alone* — what a caller who does not manage
    them gets, and the only safe answer given the wholesale semantics (see ``_manages_channels``).
    An empty list is the opposite instruction: "route nothing", which is what a reset means.

    The route's permission says "may set this scope's preferences"; only the row can say whether
    a channel belongs to that scope (CLAUDE.md §15's two-layer rule). An id from another scope is
    a 404, not a 403 — a 403 would confirm that somebody else's channel exists.
    """
    if not _manages_channels(ctx, user_id):
        return None
    in_scope = {
        config.id for config in await scope_channels(ctx.session, ctx.org.id, user_id)
    }
    writes: list[ChannelWrite] = []
    for block in payload.channels:
        if block.channel_config_id not in in_scope:
            raise AppError("not_found", "errors.not_found", status_code=404)
        writes.extend(
            ChannelWrite(
                channel_config_id=block.channel_config_id,
                event_type=row.event_type,
                enabled=row.enabled,
                delay_minutes=row.delay_minutes,
                digest=row.digest,
            )
            for row in block.events
        )
    return writes


def _writes(
    payload: PreferenceUpdate,
) -> tuple[
    list[PrefWrite],
    list[EmailWrite],
    GeneralWrite | None,
    EmailScheduleData | None,
    list[EmailWrite],
    EmailScheduleData | None,
]:
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
    email_events = [
        EmailWrite(
            event_type=row.event_type,
            enabled=row.enabled,
            delay_minutes=row.delay_minutes,
            digest=row.digest,
        )
        for row in payload.email_events
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
    email_schedule = (
        EmailScheduleData(
            digest_time=payload.email.digest_time,
            digest_weekday=payload.email.digest_weekday,
        )
        if payload.email is not None
        else None
    )
    # Web push reuses the e-mail write shapes: same three fields, same wholesale semantics. A
    # third near-identical dataclass would only be a fourth place to forget a field (#309).
    push_events = [
        EmailWrite(
            event_type=row.event_type,
            enabled=row.enabled,
            delay_minutes=row.delay_minutes,
            digest=row.digest,
        )
        for row in payload.push_events
    ]
    push_schedule = (
        EmailScheduleData(
            digest_time=payload.push.digest_time,
            digest_weekday=payload.push.digest_weekday,
        )
        if payload.push is not None
        else None
    )
    return events, email_events, general, email_schedule, push_events, push_schedule


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
    """My effective matrix: what will actually happen on both channels, and who decided it."""
    return await _load_matrix(
        ctx.session,
        ctx.org.id,
        ctx.user.id,
        include_channels=_manages_channels(ctx, ctx.user.id),
    )


@router.put(
    "/preferences",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.notification.write")],
)
async def set_preferences(
    payload: PreferenceUpdate, ctx: RequestContext = Depends(require_context)
) -> PreferenceMatrix:
    events, email_events, general, email_schedule, push_events, push_schedule = _writes(payload)
    await replace_overrides(
        ctx.session,
        ctx.org.id,
        ctx.user.id,
        events,
        email_events,
        general,
        email_schedule,
        await _channel_writes(ctx, payload, ctx.user.id),
        push_events,
        push_schedule,
    )
    return await _load_matrix(
        ctx.session,
        ctx.org.id,
        ctx.user.id,
        include_channels=_manages_channels(ctx, ctx.user.id),
    )


@router.get(
    "/preferences/defaults",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.defaults.manage")],
)
async def get_default_preferences(
    ctx: RequestContext = Depends(require_context),
) -> PreferenceMatrix:
    """What a member inherits before they override anything (org-wide), plus the shared rooms.

    A shared room is not something a member inherits and overrides — it is routed once, here, for
    everyone (#295). It rides this matrix because that is where its per-event column lives.
    """
    return await _load_matrix(
        ctx.session, ctx.org.id, None, include_channels=_manages_channels(ctx, None)
    )


@router.put(
    "/preferences/defaults",
    response_model=PreferenceMatrix,
    dependencies=[require_permission("notifications.defaults.manage")],
)
async def set_default_preferences(
    payload: PreferenceUpdate, ctx: RequestContext = Depends(require_context)
) -> PreferenceMatrix:
    events, email_events, general, email_schedule, push_events, push_schedule = _writes(payload)
    await replace_overrides(
        ctx.session,
        ctx.org.id,
        None,
        events,
        email_events,
        general,
        email_schedule,
        await _channel_writes(ctx, payload, None),
        push_events,
        push_schedule,
    )
    return await _load_matrix(
        ctx.session, ctx.org.id, None, include_channels=_manages_channels(ctx, None)
    )


# --- browser push (#309): declared before ``/{notification_id}`` -------------------------- #
# Every route here serves the caller's own devices and declares
# ``notifications.notification.write`` — registering my browser is the same act as reading and
# marking my own inbox. Deliberately **not** ``channels.manage_own``: that key gates a URL a
# person types, with an SSRF surface behind it, and the ``client`` role does not hold it. A
# subscription is minted by the person's own browser, so a portal login enrols like anyone else.
@router.get(
    "/push/config",
    response_model=PushConfig,
    dependencies=[require_permission("notifications.notification.read")],
)
async def push_config(ctx: RequestContext = Depends(require_context)) -> PushConfig:
    """The org's VAPID public key, minting the keypair on first use.

    Public by definition — it is handed to every subscribing browser as its
    ``applicationServerKey``. Fetched only when a browser is about to subscribe or is refreshing
    an already-granted subscription, so it costs nothing for the majority who never turn this on.
    """
    keys = await vapid_keys(ctx.session, ctx.org.id)
    return PushConfig(vapid_public_key=keys.public_key)


@router.get(
    "/push/subscriptions",
    response_model=list[PushSubscriptionRead],
    dependencies=[require_permission("notifications.notification.read")],
)
async def list_push_subscriptions(
    endpoint: str | None = Query(
        None,
        description="the calling browser's own endpoint, so its row can be marked `current`",
    ),
    ctx: RequestContext = Depends(require_context),
) -> list[PushSubscriptionRead]:
    """This person's registered devices. The endpoint and key material never come back — the row
    exists to be recognised and revoked, and returning it would hand any XSS a push target."""
    rows = await PushSubscriptionService(ctx).list()
    return [
        PushSubscriptionRead(
            id=row.id,
            user_agent=row.user_agent,
            created_at=row.created_at,
            last_seen_at=row.last_seen_at,
            last_success_at=row.last_success_at,
            current=endpoint is not None and row.endpoint == endpoint,
        )
        for row in rows
    ]


@router.post(
    "/push/subscriptions",
    response_model=PushSubscriptionRead,
    status_code=201,
    dependencies=[require_permission("notifications.notification.write")],
)
async def register_push_subscription(
    payload: PushSubscriptionCreate,
    ctx: RequestContext = Depends(require_context),
) -> PushSubscriptionRead:
    """Register (or refresh) the calling browser. Idempotent on the endpoint: the client
    re-presents it every session because endpoints rotate silently, and a rotated endpoint that
    nobody re-registered is a device that has stopped receiving without saying so."""
    row = await PushSubscriptionService(ctx).register(
        endpoint=payload.endpoint,
        p256dh=payload.p256dh,
        auth=payload.auth,
        user_agent=payload.user_agent,
    )
    return PushSubscriptionRead(
        id=row.id,
        user_agent=row.user_agent,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        last_success_at=row.last_success_at,
        current=True,
    )


@router.delete(
    "/push/subscriptions/{subscription_id}",
    status_code=204,
    dependencies=[require_permission("notifications.notification.write")],
)
async def revoke_push_subscription(
    subscription_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await PushSubscriptionService(ctx).revoke(subscription_id)


@router.post(
    "/push/unsubscribe",
    status_code=204,
    dependencies=[require_permission("notifications.notification.write")],
)
async def unsubscribe_push(
    payload: PushUnsubscribe,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Drop the device by the endpoint the browser itself holds — the only identifier it has
    after ``PushSubscription.unsubscribe()``. Scoped to the caller, and silent on a miss:
    unsubscribing twice is not an error."""
    await PushSubscriptionService(ctx).revoke_endpoint(payload.endpoint)


@router.post(
    "/push/test",
    response_model=PushTestResult,
    dependencies=[require_permission("notifications.notification.write")],
)
async def test_push(ctx: RequestContext = Depends(require_context)) -> PushTestResult:
    """Push a test notification to every device this person has registered.

    The one place a push leaves the API process rather than the worker cron, and worth the
    exception for the same reason the channel test-send is (#17): "did connecting this browser
    actually work?" cannot be answered by looking at the settings screen.
    """
    locale = getattr(ctx.user, "locale", None) or settings.default_locale
    delivered, error = await PushSubscriptionService(ctx).test(
        title=translate("notifications.push.test_title", locale),
        body=translate("notifications.push.test_body", locale),
    )
    return PushTestResult(ok=delivered > 0, delivered=delivered, error=error)


# --- external channels (#17, #283): declared before ``/{notification_id}`` ---------------- #
# The route floor is ``manage_own`` — every member may connect a channel *of their own*. Which
# channels a caller may see or touch is refined in the service, where the row is in hand: an org
# channel or someone else's personal channel is a **404** to a member, never a 403 that would
# confirm it exists (CLAUDE.md §15, the two-layer rule).
@router.get(
    "/channels",
    response_model=list[ChannelRead],
    dependencies=[require_permission("notifications.channels.manage_own")],
)
async def list_channels(
    ctx: RequestContext = Depends(require_context),
) -> list[ChannelRead]:
    return [ChannelRead(**c) for c in await ChannelService(ctx).list()]


@router.post(
    "/channels",
    response_model=ChannelRead,
    status_code=201,
    dependencies=[require_permission("notifications.channels.manage_own")],
)
async def create_channel(
    payload: ChannelCreate,
    ctx: RequestContext = Depends(require_context),
) -> ChannelRead:
    return ChannelRead(**await ChannelService(ctx).create(payload))


@router.patch(
    "/channels/{channel_id}",
    response_model=ChannelRead,
    dependencies=[require_permission("notifications.channels.manage_own")],
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
    dependencies=[require_permission("notifications.channels.manage_own")],
)
async def delete_channel(
    channel_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ChannelService(ctx).delete(channel_id)


@router.post(
    "/channels/{channel_id}/test",
    response_model=ChannelTestResult,
    dependencies=[require_permission("notifications.channels.manage_own")],
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
