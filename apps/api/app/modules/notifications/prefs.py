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
``visible_at`` = the next 08:00 on the org's own clock; the bell counts and the list shows
only rows whose ``visible_at`` has passed. At 08:00 the count simply jumps and the day-grouped
list *is* the digest — so "every number opens" (docs/UX.md) holds by construction.

**This module names no city.** All wall-clock reasoning happens in the org's zone
(``org_settings.timezone``, CLAUDE.md §8), which the caller resolves and passes in — a constant
here would hand every tenant somebody else's morning. Given the right zone, adding a
``timedelta`` to a zone-aware local datetime does wall-clock arithmetic, so a daily digest stays
at 08:00 across a DST transition instead of drifting an hour.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.notifications.defaults import (
    DEFAULT_DIGEST_TIME,
    DEFAULT_DUE_SOON_DAYS,
    ResolvedPref,
    default_event_pref,
)
from app.modules.notifications.events import (
    CHANNEL_EMAIL,
    CHANNEL_EXTERNAL,
    CHANNEL_IN_APP,
    CHANNEL_WEB_PUSH,
    DIGEST_HOURLY,
    DIGEST_IMMEDIATE,
    DIGEST_WEEKLY,
    EVENT_TYPES,
)
from app.modules.notifications.models import NotificationChannelConfig, NotificationPreference


# --------------------------------------------------------------------------- #
# Scheduling
# --------------------------------------------------------------------------- #
def _after_quiet_hours(slot: datetime, start: time | None, end: time | None) -> datetime:
    """Push a **local** slot out of the recipient's quiet window, to the moment it ends.

    A window that wraps midnight (22:00–07:00, the ordinary case) is as valid as one that does
    not (12:00–13:00), and they are two different comparisons — an implementation that only
    handles the first silently never fires for the second.

    Wall-clock arithmetic like everything else here: the slot is already in the org's zone, so
    "07:00" stays 07:00 on the two days a year the clocks move.
    """
    if start is None or end is None or start == end:
        return slot
    at = slot.timetz().replace(tzinfo=None)
    wraps = start > end
    inside = (at >= start or at < end) if wraps else (start <= at < end)
    if not inside:
        return slot
    out = slot.replace(hour=end.hour, minute=end.minute, second=0, microsecond=0)
    # Only a wrapping window can put the exit on tomorrow, and only for the evening half of it.
    if wraps and at >= start:
        out += timedelta(days=1)
    return out


def compute_visible_at(
    pref: ResolvedPref, now: datetime, *, tz: ZoneInfo, quiet_hours: bool = False
) -> datetime:
    """When this notification should surface, given the recipient's cadence.

    ``immediate`` honours ``delay_minutes`` (a grace period that also lets a burst of edits
    collapse into one row); the digest cadences ignore it — the cadence *is* the delay.

    ``tz`` is **the org's** zone (`app.core.timezone.org_zoneinfo`), and it is required rather
    than defaulted: "daily at 08:00" is a wall-clock promise, and a hardcoded city delivered a
    tenant in Lisbon their morning digest at 07:00 and one in Warsaw theirs at 09:00 (CLAUDE.md
    §8). The instant returned is still UTC; only the wall clock it is computed against is local.

    ``quiet_hours`` is opt-in **per channel**, and the asymmetry is the point (#309). A **pushed**
    channel passes it: e-mail, a chat room, and above all a browser notification, which is the
    first channel in this app capable of waking someone at 03:00. The **bell** does not, because
    holding an in-app row back interrupts nobody and makes the app look broken — you would open
    it, see nothing, and be told about it in the morning. The window was collected from the very
    first release (#16) and read by nothing until now; the settings hint said so out loud.
    """
    if pref.digest == DIGEST_IMMEDIATE:
        slot = now + timedelta(minutes=pref.delay_minutes) if pref.delay_minutes else now
        if not quiet_hours:
            return slot
        local = _after_quiet_hours(
            slot.astimezone(tz), pref.quiet_hours_start, pref.quiet_hours_end
        )
        return local.astimezone(UTC)

    local = now.astimezone(tz)
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
    if quiet_hours:
        slot = _after_quiet_hours(slot, pref.quiet_hours_start, pref.quiet_hours_end)
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
    #: The **in-app** general rows, borrowed when resolving some other channel. Quiet hours are
    #: one answer per person, not one per transport, and they are stored on the in-app general
    #: row because that is where the settings screen's "general" block writes them. Loaded in the
    #: same query as the channel's own rows so honouring them costs nothing (#309).
    quiet_org: NotificationPreference | None = None
    quiet_user: dict[uuid.UUID, NotificationPreference] = field(default_factory=dict)


def _quiet_window(
    user_id: uuid.UUID | None, buckets: _Buckets
) -> tuple[time | None, time | None]:
    """This scope's quiet window: the user's own row, else the org's, else none."""
    row = buckets.quiet_user.get(user_id) if user_id is not None else None
    if row is None:
        row = buckets.quiet_org
    if row is None:
        return None, None
    return row.quiet_hours_start, row.quiet_hours_end


async def _load(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    channel: str = CHANNEL_IN_APP,
    event_types: Sequence[str] | None,
    user_ids: Sequence[uuid.UUID],
) -> _Buckets:
    """One query for every row that can influence the asked-for (user, event) pairs.

    ``user_ids=[]`` loads only the org-default rows — the "what does a fresh user get" view.
    ``channel`` picks which delivery channel's rows to resolve; the bucketing is identical, so
    one loader serves every implicit channel's matrix.

    For a channel other than the bell it also pulls the **in-app general** rows, which is where
    quiet hours live for every channel (#309). One query rather than two: a pushed channel must
    not cost an extra round trip per event just to find out when not to interrupt someone.
    """
    stmt = select(NotificationPreference).where(
        NotificationPreference.org_id == org_id,
        or_(
            NotificationPreference.channel == channel,
            # The borrowed quiet-hours row; a no-op when `channel` already is the bell.
            and_(
                NotificationPreference.channel == CHANNEL_IN_APP,
                NotificationPreference.event_type.is_(None),
            ),
        ),
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
        if row.channel != channel:
            # An in-app general row loaded only for its quiet hours — never this channel's rule.
            if row.user_id is None:
                buckets.quiet_org = row
            else:
                buckets.quiet_user[row.user_id] = row
            continue
        if row.user_id is None:
            if row.event_type is None:
                buckets.org_general = row
            else:
                buckets.org_event[row.event_type] = row
        elif row.event_type is None:
            buckets.user_general[row.user_id] = row
        else:
            buckets.user_event[(row.user_id, row.event_type)] = row
    if channel == CHANNEL_IN_APP:
        # The bell's own general row *is* the quiet-hours row; keep both views in step so
        # `_quiet_window` answers the same for every channel.
        buckets.quiet_org = buckets.org_general
        buckets.quiet_user = dict(buckets.user_general)
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
# E-mail delivery (#245): per event type, mirroring the in-app matrix
# --------------------------------------------------------------------------- #
#: Off until someone opts in — e-mail is the only channel that leaves the app, so no event
#: mails by default. A per-event row (``event_type`` set, channel "email") flips it on and
#: carries its own cadence; the digest *schedule* (time-of-day + weekday) is one global choice
#: per scope, kept on the general e-mail row and folded in here.
EMAIL_PREF_OFF = ResolvedPref(
    enabled=False,
    delay_minutes=0,
    digest="daily",
    digest_time=DEFAULT_DIGEST_TIME,
    digest_weekday=None,
    channel=CHANNEL_EMAIL,
)


@dataclass(frozen=True)
class EmailSchedule:
    """A scope's global e-mail digest schedule: when its daily/weekly mails leave."""

    digest_time: time
    digest_weekday: int | None
    source: str


def _scope_schedule(user_id: uuid.UUID | None, buckets: _Buckets) -> EmailSchedule:
    """The scope's digest schedule: user general row ← org general row ← 08:00 / Monday.

    Channel-agnostic — the buckets were loaded for one channel, so this answers for that one.
    Every implicit pushed channel keeps its own schedule: someone who wants their mail at 08:00
    and their phone at 09:30 is asking for two ordinary things, not for a coupling.
    """
    row = None
    source = "default"
    if user_id is not None:
        row = buckets.user_general.get(user_id)
        if row is not None:
            source = "user"
    if row is None:
        row = buckets.org_general
        source = "org" if row is not None else "default"
    at = row.digest_time if row is not None and row.digest_time is not None else DEFAULT_DIGEST_TIME
    return EmailSchedule(
        digest_time=at,
        digest_weekday=(row.digest_weekday if row is not None else None),
        source=source,
    )


def _merge_implicit(
    event_type: str, user_id: uuid.UUID | None, buckets: _Buckets, *, off: ResolvedPref
) -> ResolvedPref:
    """The effective rule for one (user, event) on an implicit pushed channel: user ← org ← off.

    The digest schedule (time/weekday) is not per event, so it is folded in from the scope's
    general row for this channel — ``compute_visible_at`` then places a digest without another
    query. The quiet window is folded in too, from the **in-app** general row the loader
    borrowed: it is one answer per person and every pushed channel obeys the same one (#309).

    ``off`` is the channel's own "nobody has said anything" state, which differs by channel:
    e-mail and web push both default to silent, but they say so with their own ``channel`` tag.
    """
    row = None
    source = "default"
    if user_id is not None:
        row = buckets.user_event.get((user_id, event_type))
        if row is not None:
            source = "user"
    if row is None:
        row = buckets.org_event.get(event_type)
        source = "org" if row is not None else "default"

    schedule = _scope_schedule(user_id, buckets)
    quiet_start, quiet_end = _quiet_window(user_id, buckets)
    base = off
    if row is not None:
        base = replace(
            base, enabled=row.enabled, delay_minutes=row.delay_minutes, digest=row.digest
        )
    return replace(
        base,
        digest_time=schedule.digest_time,
        digest_weekday=schedule.digest_weekday,
        source=source,
        quiet_hours_start=quiet_start,
        quiet_hours_end=quiet_end,
    )


async def resolve_email_for_recipients(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    user_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ResolvedPref]:
    """The e-mail rule for one event and many recipients — a single query (no N+1)."""
    if not user_ids:
        return {}
    buckets = await _load(
        session, org_id, channel=CHANNEL_EMAIL, event_types=[event_type], user_ids=list(user_ids)
    )
    return {uid: _merge_implicit(event_type, uid, buckets, off=EMAIL_PREF_OFF) for uid in user_ids}


async def effective_email_matrix(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> tuple[dict[str, ResolvedPref], EmailSchedule]:
    """Every event's e-mail rule for one scope, plus the scope's global digest schedule.

    ``user_id=None`` yields the org defaults — the same two-scope story the in-app matrix tells.
    """
    buckets = await _load(
        session,
        org_id,
        channel=CHANNEL_EMAIL,
        event_types=None,
        user_ids=[user_id] if user_id is not None else [],
    )
    events = {
        event: _merge_implicit(event, user_id, buckets, off=EMAIL_PREF_OFF)
        for event in EVENT_TYPES
    }
    return events, _scope_schedule(user_id, buckets)


# --------------------------------------------------------------------------- #
# Web push (#309): per event type, the e-mail matrix's twin
# --------------------------------------------------------------------------- #
#: Silent for the events that were never urgent. A browser notification for something whose own
#: cadence is "tell me tomorrow at 08:00" is a phone lighting up to deliver yesterday's news.
WEB_PUSH_PREF_OFF = ResolvedPref(
    enabled=False,
    delay_minutes=0,
    digest=DIGEST_IMMEDIATE,
    digest_time=DEFAULT_DIGEST_TIME,
    digest_weekday=None,
    channel=CHANNEL_WEB_PUSH,
)

#: On for the events that are *already* immediate — a task assigned to you, a mention, an
#: overdue item, a leave decision. Granting the browser permission is the opt-in (owner call,
#: #309 follow-up): the alternative shipped a feature whose success state was a permission
#: dialog followed by silence, and a second screen nobody was sent to.
WEB_PUSH_PREF_ON = replace(WEB_PUSH_PREF_OFF, enabled=True)


def web_push_default(event_type: str) -> ResolvedPref:
    """The "nobody has said anything" rule for one event on the push channel.

    Derived from the event's **in-app** cadence rather than from a second list, so an event
    added to ``_IMMEDIATE_EVENTS`` tomorrow is pushed tomorrow and the two definitions of
    "this is urgent" cannot drift apart.

    This is the layer *under* the org row and the user row, so a tenant or a person who has
    turned an event off keeps it off: a default is what applies when nothing has been said,
    and flipping one must never overwrite something somebody said.
    """
    if default_event_pref(event_type).digest == DIGEST_IMMEDIATE:
        return WEB_PUSH_PREF_ON
    return WEB_PUSH_PREF_OFF


async def resolve_web_push_for_recipients(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    user_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, ResolvedPref]:
    """The web-push rule for one event and many recipients — a single query (no N+1)."""
    if not user_ids:
        return {}
    buckets = await _load(
        session,
        org_id,
        channel=CHANNEL_WEB_PUSH,
        event_types=[event_type],
        user_ids=list(user_ids),
    )
    off = web_push_default(event_type)
    return {uid: _merge_implicit(event_type, uid, buckets, off=off) for uid in user_ids}


async def effective_web_push_matrix(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> tuple[dict[str, ResolvedPref], EmailSchedule]:
    """Every event's web-push rule for one scope, plus that scope's push digest schedule."""
    buckets = await _load(
        session,
        org_id,
        channel=CHANNEL_WEB_PUSH,
        event_types=None,
        user_ids=[user_id] if user_id is not None else [],
    )
    events = {
        event: _merge_implicit(event, user_id, buckets, off=web_push_default(event))
        for event in EVENT_TYPES
    }
    return events, _scope_schedule(user_id, buckets)


# --------------------------------------------------------------------------- #
# External channels (#283, #295): per event, per channel
# --------------------------------------------------------------------------- #
#: Off until someone routes an event here. Every channel is opt-in per event — that is what
#: replaced ``event_filter``, and "connected but silent" is the only safe default for a transport
#: that pings someone's phone or a room the whole team watches.
CHANNEL_PREF_OFF = ResolvedPref(
    enabled=False,
    delay_minutes=0,
    digest=DIGEST_IMMEDIATE,
    digest_time=DEFAULT_DIGEST_TIME,
    digest_weekday=None,
    channel=CHANNEL_EXTERNAL,
)


async def _load_channel_rows(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    channel_ids: Sequence[uuid.UUID],
    event_types: Sequence[str] | None,
) -> dict[tuple[uuid.UUID, str], NotificationPreference]:
    """One query for every per-channel row that can influence the asked-for pairs.

    Not scoped by ``user_id``: a channel belongs to exactly one scope, so its id already says
    whose rows these are. Adding the filter would only be a way to get it wrong.
    """
    if not channel_ids:
        return {}
    stmt = select(NotificationPreference).where(
        NotificationPreference.org_id == org_id,
        NotificationPreference.channel == CHANNEL_EXTERNAL,
        NotificationPreference.channel_config_id.in_(list(channel_ids)),
        NotificationPreference.event_type.is_not(None),
    )
    if event_types is not None:
        stmt = stmt.where(NotificationPreference.event_type.in_(list(event_types)))
    rows = (await session.execute(stmt)).scalars().all()
    return {(row.channel_config_id, row.event_type): row for row in rows}


async def _quiet_windows(
    session: AsyncSession, org_id: uuid.UUID, user_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID | None, NotificationPreference]:
    """The in-app general rows that carry quiet hours, keyed by owner (``None`` = the org's).

    One query for the whole batch. Its own loader rather than `_load`'s because the external
    path resolves per *channel*, not per user, and the set of people it needs windows for is
    "the owners of these channels" — which `_load`'s (user, event) bucketing does not model.
    """
    stmt = select(NotificationPreference).where(
        NotificationPreference.org_id == org_id,
        NotificationPreference.channel == CHANNEL_IN_APP,
        NotificationPreference.event_type.is_(None),
        NotificationPreference.channel_config_id.is_(None),
        or_(
            NotificationPreference.user_id.in_(list(user_ids)),
            NotificationPreference.user_id.is_(None),
        ),
    )
    rows = (await session.execute(stmt)).scalars().all()
    return {row.user_id: row for row in rows}


def _merge_channel(
    row: NotificationPreference | None,
    config: NotificationChannelConfig,
    quiet: dict[uuid.UUID | None, NotificationPreference] | None = None,
) -> ResolvedPref:
    """One (channel, event) rule: the row if there is one, else off.

    Flat, unlike the implicit channels: a channel is owned by exactly one scope, so there is
    nothing to inherit *from*. Nobody but the owner has an opinion about their own Slack DM, and a
    shared room's routing is the org's single answer, not a default someone overrides.

    The digest *schedule* (time-of-day, weekday) is not per event, so it is folded in from the
    channel itself, exactly as the e-mail matrix folds in the scope's general row.

    Quiet hours follow the same ownership rule (#309), and it is the reason they are resolved here
    rather than assumed: a **personal** channel obeys *its owner's* window (their DM, their night),
    a **shared room** obeys the *org's* — a room has no single person whose night it is, so the
    org's own answer is the only one that means anything. Without this the external channel would
    pass `quiet_hours=True` into `compute_visible_at` and silently change nothing, because the
    window would always be `None`.
    """
    base = CHANNEL_PREF_OFF
    if row is not None:
        base = replace(
            base,
            enabled=row.enabled,
            delay_minutes=row.delay_minutes,
            digest=row.digest,
            source="org" if config.user_id is None else "user",
        )
    window = None
    if quiet is not None:
        window = quiet.get(config.user_id) if config.user_id is not None else None
        if window is None:
            window = quiet.get(None)
    return replace(
        base,
        digest_time=config.digest_time or DEFAULT_DIGEST_TIME,
        digest_weekday=config.digest_weekday,
        quiet_hours_start=window.quiet_hours_start if window is not None else None,
        quiet_hours_end=window.quiet_hours_end if window is not None else None,
    )


async def resolve_channel_prefs(
    session: AsyncSession,
    org_id: uuid.UUID,
    event_type: str,
    configs: Sequence[NotificationChannelConfig],
) -> dict[uuid.UUID, ResolvedPref]:
    """The rule for one event on each of these channels — two queries, never one per channel."""
    if not configs:
        return {}
    rows = await _load_channel_rows(
        session, org_id, channel_ids=[c.id for c in configs], event_types=[event_type]
    )
    quiet = await _quiet_windows(
        session, org_id, [c.user_id for c in configs if c.user_id is not None]
    )
    return {c.id: _merge_channel(rows.get((c.id, event_type)), c, quiet) for c in configs}


async def effective_channel_matrix(
    session: AsyncSession,
    org_id: uuid.UUID,
    configs: Sequence[NotificationChannelConfig],
) -> dict[uuid.UUID, dict[str, ResolvedPref]]:
    """Every event's rule on every one of a scope's channels — one query for all of it.

    No quiet window folded in, deliberately: this feeds the settings *matrix*, which renders what
    each event is routed at. Quiet hours are one scope-wide control on the same screen, not a
    per-cell value, and asking for them here would cost a query to display nothing new.
    """
    if not configs:
        return {}
    rows = await _load_channel_rows(
        session, org_id, channel_ids=[c.id for c in configs], event_types=None
    )
    return {
        config.id: {
            event: _merge_channel(rows.get((config.id, event)), config) for event in EVENT_TYPES
        }
        for config in configs
    }


async def scope_channels(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID | None
) -> list[NotificationChannelConfig]:
    """One scope's external channels, in the order the matrix renders their columns (#295).

    ``user_id`` set → that person's own transports; ``user_id=None`` → the org's shared rooms.
    A channel is a column of exactly one matrix, and it is this function that says which: routing
    to somebody's Slack DM is not something a manager can pre-decide, and how noisy ``#crm`` is is
    not something whoever last opened their own settings gets to decide.
    """
    scope = (
        NotificationChannelConfig.user_id.is_(None)
        if user_id is None
        else NotificationChannelConfig.user_id == user_id
    )
    return list(
        (
            await session.execute(
                select(NotificationChannelConfig)
                .where(NotificationChannelConfig.org_id == org_id, scope)
                .order_by(NotificationChannelConfig.name)
            )
        )
        .scalars()
        .all()
    )


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


@dataclass(frozen=True)
class EmailWrite:
    """One event's e-mail override at a scope. The schedule is global, so no time/weekday here."""

    event_type: str
    enabled: bool
    delay_minutes: int
    digest: str


@dataclass(frozen=True)
class EmailScheduleWrite:
    """A scope's global digest schedule (time-of-day + weekday) for one implicit channel."""

    digest_time: time | None
    digest_weekday: int | None


@dataclass(frozen=True)
class ChannelWrite:
    """One event routed to one external channel (#283, #295).

    No time/weekday: a channel's digest schedule lives on the channel itself, so a person who
    wants their Slack digest at 09:00 says it once rather than on every row.
    """

    channel_config_id: uuid.UUID
    event_type: str
    enabled: bool
    delay_minutes: int
    digest: str


async def replace_overrides(
    session: AsyncSession,
    org_id: uuid.UUID,
    user_id: uuid.UUID | None,
    events: Sequence[PrefWrite],
    email_events: Sequence[EmailWrite],
    general: GeneralWrite | None,
    email_schedule: EmailScheduleWrite | None,
    channel_events: Sequence[ChannelWrite] | None = None,
    web_push_events: Sequence[EmailWrite] = (),
    web_push_schedule: EmailScheduleWrite | None = None,
) -> None:
    """Set this scope's rows to exactly the given in-app + e-mail + push + per-channel overrides.

    Delete-then-insert rather than a diff: the partial unique indexes make an interleaved
    update/insert awkward, and a scope holds at most one row per (event, channel). Every channel
    is rewritten in one pass — the caller resolves each one's overrides independently, so an
    event may override e-mail while its in-app rule keeps inheriting, and vice versa.

    Channel rows follow their channel's scope (#295): a user's own transports write user rows, the
    org's shared rooms write org rows. The caller has already verified that every
    ``channel_config_id`` belongs to *this* scope — the service layer owns that check, where the
    row is in hand.

    ``channel_events=None`` leaves this scope's channel rows **untouched**, which is not the same
    as ``()``. An empty sequence is the wholesale instruction "route nothing" (a reset); ``None``
    is "this caller does not manage these channels, so their silence says nothing about them".
    Collapsing the two would let a caller who cannot even see the columns clear every route by
    saving an unrelated part of the matrix.
    """
    scope = (
        NotificationPreference.user_id.is_(None)
        if user_id is None
        else NotificationPreference.user_id == user_id
    )
    await session.execute(
        delete(NotificationPreference).where(
            NotificationPreference.org_id == org_id,
            NotificationPreference.channel.in_(
                [CHANNEL_IN_APP, CHANNEL_EMAIL, CHANNEL_WEB_PUSH]
            ),
            NotificationPreference.channel_config_id.is_(None),
            scope,
        )
    )
    if channel_events is not None:
        # The per-channel rows are a separate quadrant with its own unique index, so they need
        # their own wholesale delete: an unrouted event is an absent row, not a disabled one.
        await session.execute(
            delete(NotificationPreference).where(
                NotificationPreference.org_id == org_id,
                NotificationPreference.channel == CHANNEL_EXTERNAL,
                NotificationPreference.channel_config_id.is_not(None),
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
    for event in email_events:
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                event_type=event.event_type,
                channel=CHANNEL_EMAIL,
                enabled=event.enabled,
                delay_minutes=event.delay_minutes,
                digest=event.digest,
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
    if email_schedule is not None:
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                event_type=None,
                channel=CHANNEL_EMAIL,
                digest_time=email_schedule.digest_time,
                digest_weekday=email_schedule.digest_weekday,
            )
        )
    for event in web_push_events:
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                event_type=event.event_type,
                channel=CHANNEL_WEB_PUSH,
                enabled=event.enabled,
                delay_minutes=event.delay_minutes,
                digest=event.digest,
            )
        )
    if web_push_schedule is not None:
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                event_type=None,
                channel=CHANNEL_WEB_PUSH,
                digest_time=web_push_schedule.digest_time,
                digest_weekday=web_push_schedule.digest_weekday,
            )
        )
    for row in channel_events or ():
        session.add(
            NotificationPreference(
                org_id=org_id,
                user_id=user_id,
                channel_config_id=row.channel_config_id,
                event_type=row.event_type,
                channel=CHANNEL_EXTERNAL,
                enabled=row.enabled,
                delay_minutes=row.delay_minutes,
                digest=row.digest,
            )
        )
    await session.flush()
