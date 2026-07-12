"""Preference resolution and digest scheduling (issue #16).

Three layers, most specific wins, **whole row** at a time: hardcoded default ← org-default row
(``user_id IS NULL``) ← user row. Overriding one field means writing a row; resetting means
deleting it. That keeps "what will actually happen to me" answerable from a single row rather
than a per-field merge nobody can predict.

Two scopes share the table: an **event row** (``event_type`` set) carries enable/delay/digest,
and a **general row** (``event_type IS NULL``) carries the values that are not per-event —
quiet hours and the due-soon threshold.

Scheduling is expressed entirely as ``visible_at``: there is no digest cron and no synthetic
digest row. A daily-digest event writes its notification row immediately with
``visible_at`` = the next 08:00 Europe/Amsterdam; the bell counts and the list shows only rows
whose ``visible_at`` has passed. At 08:00 the count simply jumps and the day-grouped list *is*
the digest — so "every number opens" (docs/UX.md) holds by construction.

All wall-clock reasoning happens in ``Europe/Amsterdam`` (the platform timezone, CLAUDE.md §8);
adding a ``timedelta`` to a zone-aware local datetime does wall-clock arithmetic, so a daily
digest stays at 08:00 across a DST transition instead of drifting an hour.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.defaults import (
    DEFAULT_DIGEST_TIME,
    DEFAULT_DUE_SOON_DAYS,
    ResolvedPref,
    default_event_pref,
)
from app.modules.notifications.events import (
    CHANNEL_EMAIL,
    CHANNEL_IN_APP,
    DIGEST_HOURLY,
    DIGEST_IMMEDIATE,
    DIGEST_WEEKLY,
    EVENT_TYPES,
)
from app.modules.notifications.models import NotificationPreference

AMSTERDAM = ZoneInfo("Europe/Amsterdam")


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
def compute_visible_at(pref: ResolvedPref, now: datetime) -> datetime:
    """When this notification should surface in the bell, given the recipient's cadence.

    ``immediate`` honours ``delay_minutes`` (a grace period that also lets a burst of edits
    collapse into one row); the digest cadences ignore it — the cadence *is* the delay.
    """
    if pref.digest == DIGEST_IMMEDIATE:
        return now + timedelta(minutes=pref.delay_minutes) if pref.delay_minutes else now

    local = now.astimezone(AMSTERDAM)
    if pref.digest == DIGEST_HOURLY:
        slot = local.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    elif pref.digest == DIGEST_WEEKLY:
        at = pref.digest_time or DEFAULT_DIGEST_TIME
        weekday = pref.digest_weekday if pref.digest_weekday is not None else 0
        slot = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        slot += timedelta(days=(weekday - slot.weekday()) % 7)
        if slot <= local:
            slot += timedelta(days=7)
    else:  # daily — and any unknown cadence degrades to it rather than vanishing
        at = pref.digest_time or DEFAULT_DIGEST_TIME
        slot = local.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
        if slot <= local:
            slot += timedelta(days=1)
    return slot.astimezone(UTC)


# --------------------------------------------------------------------------- #
# Resolution
# --------------------------------------------------------------------------- #
@dataclass
class _Buckets:
    org_event: dict[str, NotificationPreference]
    org_general: NotificationPreference | None
    user_event: dict[tuple[uuid.UUID, str], NotificationPreference]
    user_general: dict[uuid.UUID, NotificationPreference]


async def _load(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    event_types: Sequence[str] | None,
    user_ids: Sequence[uuid.UUID],
) -> _Buckets:
    """One query for every row that can influence the asked-for (user, event) pairs.

    ``user_ids=[]`` loads only the org-default rows — the "what does a fresh user get" view.
    """
    stmt = select(NotificationPreference).where(
        NotificationPreference.org_id == org_id,
        NotificationPreference.channel == CHANNEL_IN_APP,
        or_(
            NotificationPreference.user_id.in_(user_ids),
            NotificationPreference.user_id.is_(None),
        ),
    )
    if event_types is not None:
        stmt = stmt.where(
            or_(
                NotificationPreference.event_type.in_(event_types),
                NotificationPreference.event_type.is_(None),
            )
        )
    rows = (await session.execute(stmt)).scalars().all()

    buckets = _Buckets({}, None, {}, {})
    for row in rows:
        if row.user_id is None:
            if row.event_type is None:
                buckets.org_general = row
            else:
                buckets.org_event[row.event_type] = row
        elif row.event_type is None:
            buckets.user_general[row.user_id] = row
        else:
            buckets.user_event[(row.user_id, row.event_type)] = row
    return buckets


def _merge(event_type: str, user_id: uuid.UUID | None, buckets: _Buckets) -> ResolvedPref:
    """Pick the winning event row and the winning general row, and fuse them."""
    row = None
    source = "default"
    if user_id is not None:
        row = buckets.user_event.get((user_id, event_type))
        if row is not None:
            source = "user"
    if row is None:
        row = buckets.org_event.get(event_type)
        source = "org" if row is not None else "default"

    general = None
    general_source = "default"
    if user_id is not None:
        general = buckets.user_general.get(user_id)
        if general is not None:
            general_source = "user"
    if general is None:
        general = buckets.org_general
        general_source = "org" if general is not None else "default"

    base = default_event_pref(event_type)
    if row is not None:
        base = replace(
            base,
            enabled=row.enabled,
            delay_minutes=row.delay_minutes,
            digest=row.digest,
            digest_time=row.digest_time,
            digest_weekday=row.digest_weekday,
        )
    due_soon = DEFAULT_DUE_SOON_DAYS
    if general is not None and general.due_soon_days is not None:
        due_soon = general.due_soon_days
    return replace(
        base,
        source=source,
        due_soon_days=due_soon,
        quiet_hours_start=general.quiet_hours_start if general is not None else None,
        quiet_hours_end=general.quiet_hours_end if general is not None else None,
        general_source=general_source,
    )


async def resolve_for_recipients(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    user_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ResolvedPref]:
    """The effective rule for one event and many recipients — a single query (no N+1)."""
    if not user_ids:
        return {}
    buckets = await _load(session, org_id, event_types=[event_type], user_ids=list(user_ids))
    return {uid: _merge(event_type, uid, buckets) for uid in user_ids}


async def effective_matrix(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> dict[str, ResolvedPref]:
    """The whole event matrix for one user, or the org defaults when ``user_id`` is None.

    Every event is present, so the settings screen renders a complete table and each row can
    badge whether it is inherited (``source`` = ``default``/``org``) or an explicit override.
    """
    buckets = await _load(
        session,
        org_id,
        event_types=None,
        user_ids=[user_id] if user_id is not None else [],
    )
    return {event: _merge(event, user_id, buckets) for event in EVENT_TYPES}


# --------------------------------------------------------------------------- #
# E-mail delivery (#17): one *general* row per user on channel "email"
# --------------------------------------------------------------------------- #
#: Off until someone opts in — e-mail is the only channel that leaves the app, so it is
#: never on by default. The cadence fields still carry sane values for the settings form.
EMAIL_PREF_OFF = ResolvedPref(
    enabled=False,
    delay_minutes=0,
    digest="daily",
    digest_time=DEFAULT_DIGEST_TIME,
    digest_weekday=None,
    channel=CHANNEL_EMAIL,
)


async def email_prefs_for_recipients(
    session: AsyncSession, org_id: uuid.UUID, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ResolvedPref]:
    """The e-mail rule per recipient: the user's general row, else the org default row, else off.

    Deliberately *not* per event type: "mail me my notifications, at this cadence" is one
    decision. The per-event nuance stays on the in-app matrix.
    """
    if not user_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.org_id == org_id,
                    NotificationPreference.channel == CHANNEL_EMAIL,
                    NotificationPreference.event_type.is_(None),
                    or_(
                        NotificationPreference.user_id.in_(list(user_ids)),
                        NotificationPreference.user_id.is_(None),
                    ),
                )
            )
        )
        .scalars()
        .all()
    )
    org_row = next((r for r in rows if r.user_id is None), None)
    by_user = {r.user_id: r for r in rows if r.user_id is not None}

    def resolved(row: NotificationPreference | None) -> ResolvedPref:
        if row is None:
            return EMAIL_PREF_OFF
        return ResolvedPref(
            enabled=row.enabled,
            delay_minutes=row.delay_minutes,
            digest=row.digest,
            digest_time=row.digest_time,
            digest_weekday=row.digest_weekday,
            channel=CHANNEL_EMAIL,
            source="user" if row.user_id is not None else "org",
        )

    return {uid: resolved(by_user.get(uid, org_row)) for uid in user_ids}


async def save_email_pref(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    enabled: bool,
    digest: str,
    digest_time: time | None,
    digest_weekday: int | None,
) -> None:
    """Upsert the user's general e-mail row (one per user, enforced by the partial unique)."""
    row = await session.scalar(
        select(NotificationPreference).where(
            NotificationPreference.org_id == org_id,
            NotificationPreference.channel == CHANNEL_EMAIL,
            NotificationPreference.event_type.is_(None),
            NotificationPreference.user_id == user_id,
        )
    )
    if row is None:
        row = NotificationPreference(
            org_id=org_id, user_id=user_id, event_type=None, channel=CHANNEL_EMAIL
        )
        session.add(row)
    row.enabled = enabled
    row.digest = digest
    row.digest_time = digest_time
    row.digest_weekday = digest_weekday
    await session.flush()


# --------------------------------------------------------------------------- #
# Published interface (CLAUDE.md §6) — the one sanctioned cross-module crossing
# --------------------------------------------------------------------------- #
async def due_soon_thresholds(
    session: AsyncSession, org_id: uuid.UUID
) -> dict[uuid.UUID | None, int]:
    """How many days ahead each user considers a task "due soon".

    The tasks module's reminder cron asks *notifications* this rather than reading its tables
    (Golden Rule 3): it emits ``task.due_soon`` on exactly the day that matches the assignee's
    threshold. The ``None`` key carries the org-wide default for users with no override, so a
    caller resolves with ``thresholds.get(user_id, thresholds[None])``.
    """
    rows = (
        (
            await session.execute(
                select(NotificationPreference).where(
                    NotificationPreference.org_id == org_id,
                    NotificationPreference.channel == CHANNEL_IN_APP,
                    NotificationPreference.event_type.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )

    org_default = DEFAULT_DUE_SOON_DAYS
    for row in rows:
        if row.user_id is None and row.due_soon_days is not None:
            org_default = row.due_soon_days
    thresholds: dict[uuid.UUID | None, int] = {None: org_default}
    for row in rows:
        if row.user_id is not None and row.due_soon_days is not None:
            thresholds[row.user_id] = row.due_soon_days
    return thresholds


# --------------------------------------------------------------------------- #
# Writes — a PUT replaces that scope's rows wholesale (absent row = inherit)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PrefWrite:
    event_type: str
    enabled: bool
    delay_minutes: int
    digest: str
    digest_time: time | None
    digest_weekday: int | None


@dataclass(frozen=True)
class GeneralWrite:
    due_soon_days: int | None
    quiet_hours_start: time | None
    quiet_hours_end: time | None


async def replace_overrides(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    events: Sequence[PrefWrite],
    general: GeneralWrite | None,
) -> None:
    """Set this scope's rows to exactly ``events`` (+ ``general``); everything else inherits.

    Delete-then-insert rather than a diff: the partial unique indexes make an interleaved
    update/insert awkward, and a scope holds at most one row per event.
    """
    scope = (
        NotificationPreference.user_id.is_(None)
        if user_id is None
        else NotificationPreference.user_id == user_id
    )
    await session.execute(
        delete(NotificationPreference).where(
            NotificationPreference.org_id == org_id,
            NotificationPreference.channel == CHANNEL_IN_APP,
            scope,
        )
    )
    await session.flush()  # clear the partial unique indexes before re-claiming them

    for event in events:
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                event_type=event.event_type,
                channel=CHANNEL_IN_APP,
                enabled=event.enabled,
                delay_minutes=event.delay_minutes,
                digest=event.digest,
                digest_time=event.digest_time,
                digest_weekday=event.digest_weekday,
            )
        )
    if general is not None:
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                event_type=None,
                channel=CHANNEL_IN_APP,
                due_soon_days=general.due_soon_days,
                quiet_hours_start=general.quiet_hours_start,
                quiet_hours_end=general.quiet_hours_end,
            )
        )
    await session.flush()
