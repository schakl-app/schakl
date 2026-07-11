"""Fan-out and inbox reads (issue #16).

**One subscriber for every event.** Emitting modules resolve their *own* audience (an
assignee list, a project roster, the org's managers) into the reserved ``_recipients`` payload
key; this service adds the record's watchers, subtracts anyone who muted it, subtracts the
actor, applies each survivor's delivery preference, and writes the rows. No module ever
imports another's models (Golden Rule 3) — the payload is the whole contract.

The fan-out runs **inline in the emitter's transaction** (``app/core/events.py``), so an event
and the notifications it produces commit or roll back together. That is why ``ingest`` does no
network I/O and issues a bounded number of queries — a dedup probe, the watchers, the
preferences, the collapse probe, then one bulk insert — regardless of recipient count.

Three rules that are easy to get wrong, so they are enforced here rather than at each emit
site:

* **Never notify the actor** about their own action.
* **A commenter becomes a watcher**, so the conversation keeps reaching them.
* **A reminder nobody wants is not history**: a system (cron) event with no surviving
  recipient writes no event row, while a user action always does — the activity feed shows
  what happened even when nobody asked to hear about it.

The in-app channel is *pull*: the ``notifications`` row is the delivery (see ``channels.py``,
the seam issue #17 plugs external transports into).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update

from app.core.auth.models import User
from app.core.events import EmitContext
from app.core.models import Membership
from app.errors import AppError
from app.modules.notifications.events import (
    DEDUP_KEY,
    ENTITY_FOR_EVENT,
    ENTITY_TYPES,
    PROJECT_BUDGET_THRESHOLD,
    RECIPIENTS_KEY,
    TASK_COMMENTED,
    TASK_DUE_SOON,
    TASK_OVERDUE,
    TIME_TIMESHEET_REMINDER,
)
from app.modules.notifications.models import (
    Notification,
    NotificationEvent,
    NotificationWatcher,
)
from app.modules.notifications.prefs import compute_visible_at, resolve_for_recipients

#: Cron-driven events. They describe a *state*, not an act, so an event nobody wants to hear
#: about is not worth recording; a user action is, because the activity feed shows it.
_SYSTEM_EVENTS: frozenset[str] = frozenset(
    {TASK_DUE_SOON, TASK_OVERDUE, PROJECT_BUDGET_THRESHOLD, TIME_TIMESHEET_REMINDER}
)

#: Where each event carries its subject's id. Emitters already speak these names (the
#: companies module has published ``company_id`` since P0), so nothing had to be renamed.
ENTITY_ID_KEY: dict[str, str] = {
    "task.assigned": "task_id",
    "task.unassigned": "task_id",
    "task.status_changed": "task_id",
    "task.commented": "task_id",
    "task.mentioned": "task_id",
    "task.due_soon": "task_id",
    "task.overdue": "task_id",
    "project.assigned": "project_id",
    "project.status_changed": "project_id",
    "project.budget_threshold": "project_id",
    "company.created": "company_id",
    "company.status_changed": "company_id",
    "company.assigned": "company_id",
    "leave.requested": "leave_request_id",
    "leave.approved": "leave_request_id",
    "leave.rejected": "leave_request_id",
    # A timesheet has no row of its own; it is a person's week, so the person is the subject.
    "time.entry_approved": "user_id",
    "time.timesheet_reminder": "user_id",
}

SORTABLE: dict[str, Any] = {"created_at": Notification.created_at}


def _jsonable(value: Any) -> Any:
    """Coerce a payload to something JSONB accepts (UUIDs, dates and Decimals are common)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set):
        return [_jsonable(item) for item in value]
    return value


def _as_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _display_name(full_name: str | None, email: str | None) -> str | None:
    """How the UI names a person; ``None`` means the system acted."""
    return (full_name or email) if email is not None else None


class NotificationService:
    """Tenant-scoped. ``ctx`` is a ``RequestContext`` for reads and a request-borne fan-out,
    or a ``SystemContext`` when a cron emits (then ``user`` is None and nobody is the actor).
    """

    def __init__(self, ctx: EmitContext) -> None:
        self.ctx = ctx

    @property
    def org_id(self) -> uuid.UUID:
        return self.ctx.org.id

    @property
    def session(self):  # noqa: ANN201 - AsyncSession, typed via the context protocol
        return self.ctx.session

    @property
    def actor_id(self) -> uuid.UUID | None:
        user = getattr(self.ctx, "user", None)
        return user.id if user is not None else None

    # ----------------------------------------------------------------- #
    # Fan-out
    # ----------------------------------------------------------------- #
    async def ingest(
        self,
        event_type: str,
        entity_type: str,
        entity_id: uuid.UUID,
        payload: dict[str, Any],
    ) -> NotificationEvent | None:
        """Record an event and deliver it to everyone who should hear about it.

        Returns the event row, or ``None`` when the event was deduplicated away or was a
        system reminder with no audience. Never raises on an empty audience.
        """
        # Copy: the same payload dict is handed to every subscriber, and the reserved keys are
        # routing rather than content — popping them from the caller's dict would corrupt the
        # next handler (the tasks module's company-status automation shares these events).
        data = dict(payload)
        hinted = data.pop(RECIPIENTS_KEY, None) or []
        dedup_key = data.pop(DEDUP_KEY, None)
        now = datetime.now(UTC)

        if dedup_key is not None and await self._dedup_exists(dedup_key):
            return None

        # Commenting on a record subscribes you to it.
        if event_type == TASK_COMMENTED and self.actor_id is not None:
            await self._auto_watch(entity_type, entity_id, self.actor_id)

        watching, muted = await self._watchers(entity_type, entity_id)
        recipients = {uid for uid in (_as_uuid(u) for u in hinted) if uid is not None}
        recipients |= watching
        recipients -= muted
        recipients.discard(self.actor_id)
        # A hint is data from another module; only people who can actually open the record
        # may be told about it (Golden Rule 1 — never trust the payload for authorization).
        recipients = await self._members_only(recipients)

        delivery = await self._apply_preferences(event_type, recipients, data, now)

        # A reminder nobody subscribed to leaves no trace; a person's action always does.
        if not delivery and event_type in _SYSTEM_EVENTS:
            return None

        event = NotificationEvent(
            org_id=self.org_id,
            event_type=event_type,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=self.actor_id,
            payload=_jsonable(data),
            dedup_key=dedup_key,
        )
        self.session.add(event)
        await self.session.flush()

        await self._deliver(event, delivery, now)
        return event

    async def _members_only(self, recipients: set[uuid.UUID]) -> set[uuid.UUID]:
        """Drop anyone who is not a member of this org — one query, no per-recipient check."""
        if not recipients:
            return recipients
        rows = await self.session.execute(
            select(Membership.user_id).where(
                Membership.org_id == self.org_id,
                Membership.user_id.in_(recipients),
            )
        )
        return set(rows.scalars())

    async def _apply_preferences(
        self,
        event_type: str,
        recipients: set[uuid.UUID],
        data: dict[str, Any],
        now: datetime,
    ) -> list[tuple[uuid.UUID, datetime]]:
        """Drop recipients who switched this event off, and schedule the rest."""
        if not recipients:
            return []
        resolved = await resolve_for_recipients(
            self.session, self.org_id, event_type, sorted(recipients)
        )
        days_left = data.get("days_left")
        delivery: list[tuple[uuid.UUID, datetime]] = []
        for user_id in sorted(recipients):
            pref = resolved[user_id]
            if not pref.enabled:
                continue
            # "Due soon" is per-person: the cron offers the candidate, the threshold decides.
            if event_type == TASK_DUE_SOON and pref.due_soon_days != days_left:
                continue
            delivery.append((user_id, compute_visible_at(pref, now)))
        return delivery

    async def _deliver(
        self,
        event: NotificationEvent,
        delivery: Sequence[tuple[uuid.UUID, datetime]],
        now: datetime,
    ) -> None:
        """Write one inbox row per recipient, collapsing bursts into the pending row.

        If a recipient still has an unread, *not yet visible* row for the same event type on
        the same record, the new event replaces it rather than stacking: five status flips
        before the digest fires should read as one line, at the time the first one was due.
        """
        if not delivery:
            return
        user_ids = [user_id for user_id, _ in delivery]
        pending = {
            row.user_id: row
            for row in (
                await self.session.execute(
                    select(Notification)
                    .join(NotificationEvent, Notification.event_id == NotificationEvent.id)
                    .where(
                        Notification.org_id == self.org_id,
                        Notification.user_id.in_(user_ids),
                        Notification.read_at.is_(None),
                        Notification.visible_at > now,
                        NotificationEvent.event_type == event.event_type,
                        NotificationEvent.entity_type == event.entity_type,
                        NotificationEvent.entity_id == event.entity_id,
                        NotificationEvent.id != event.id,
                    )
                )
            ).scalars()
        }

        fresh: list[Notification] = []
        for user_id, visible_at in delivery:
            collapsed = pending.get(user_id)
            if collapsed is not None:
                collapsed.event_id = event.id  # newest content, original schedule
                continue
            row = Notification(
                org_id=self.org_id,
                event_id=event.id,
                user_id=user_id,
                visible_at=visible_at,
            )
            self.session.add(row)
            fresh.append(row)
        await self.session.flush()

        # Hand each fresh notification to every registered channel. The in-app channel is pull
        # (a no-op); the external channel (#17) enqueues delivery rows for the worker to push.
        from app.modules.notifications.channels import channels as all_channels

        for channel in all_channels():
            for row in fresh:
                await channel.deliver(
                    self.ctx,
                    notification_id=row.id,
                    user_id=row.user_id,
                    event_type=event.event_type,
                )

    async def _dedup_exists(self, dedup_key: str) -> bool:
        found = await self.session.scalar(
            select(NotificationEvent.id)
            .where(
                NotificationEvent.org_id == self.org_id,
                NotificationEvent.dedup_key == dedup_key,
            )
            .limit(1)
        )
        return found is not None

    async def _watchers(
        self, entity_type: str, entity_id: uuid.UUID
    ) -> tuple[set[uuid.UUID], set[uuid.UUID]]:
        rows = (
            await self.session.execute(
                select(NotificationWatcher.user_id, NotificationWatcher.muted).where(
                    NotificationWatcher.org_id == self.org_id,
                    NotificationWatcher.entity_type == entity_type,
                    NotificationWatcher.entity_id == entity_id,
                )
            )
        ).all()
        watching = {row.user_id for row in rows if not row.muted}
        muted = {row.user_id for row in rows if row.muted}
        return watching, muted

    async def _auto_watch(
        self, entity_type: str, entity_id: uuid.UUID, user_id: uuid.UUID
    ) -> None:
        """Follow a record on first comment — but never resurrect an explicit mute."""
        existing = await self.session.scalar(
            select(NotificationWatcher).where(
                NotificationWatcher.org_id == self.org_id,
                NotificationWatcher.user_id == user_id,
                NotificationWatcher.entity_type == entity_type,
                NotificationWatcher.entity_id == entity_id,
            )
        )
        if existing is not None:
            return
        self.session.add(
            NotificationWatcher(
                org_id=self.org_id,
                user_id=user_id,
                entity_type=entity_type,
                entity_id=entity_id,
                muted=False,
            )
        )
        await self.session.flush()

    # ----------------------------------------------------------------- #
    # Inbox reads (always the caller's own rows, always already visible)
    # ----------------------------------------------------------------- #
    def _visible(self):  # noqa: ANN202
        return (
            select(Notification, NotificationEvent, User.full_name, User.email)
            .join(NotificationEvent, Notification.event_id == NotificationEvent.id)
            .outerjoin(User, User.id == NotificationEvent.actor_user_id)
            .where(
                Notification.org_id == self.org_id,
                Notification.user_id == self.ctx.user.id,
                Notification.visible_at <= func.now(),
            )
        )

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        unread: bool | None = None,
        entity_type: str | None = None,
        sort: str | None = None,
        count: bool = True,
    ) -> tuple[list[dict[str, Any]], int]:
        from app.core.sorting import apply_sort

        stmt = self._visible()
        if unread is True:
            stmt = stmt.where(Notification.read_at.is_(None))
        elif unread is False:
            stmt = stmt.where(Notification.read_at.is_not(None))
        if entity_type is not None:
            stmt = stmt.where(NotificationEvent.entity_type == entity_type)

        total = 0
        if count:
            subquery = stmt.with_only_columns(Notification.id).order_by(None).subquery()
            total = int(
                await self.session.scalar(select(func.count()).select_from(subquery)) or 0
            )

        stmt = apply_sort(stmt, sort, SORTABLE, default=Notification.created_at.desc())
        rows = (await self.session.execute(stmt.limit(limit).offset(offset))).all()
        return [self._read(row) for row in rows], total

    @staticmethod
    def _read(row: Any) -> dict[str, Any]:
        notification, event, full_name, email = row
        return {
            "id": notification.id,
            "event_type": event.event_type,
            "entity_type": event.entity_type,
            "entity_id": event.entity_id,
            "actor_name": _display_name(full_name, email),
            "payload": event.payload,
            "read_at": notification.read_at,
            "visible_at": notification.visible_at,
            "created_at": notification.created_at,
        }

    async def unread_count(self) -> int:
        return int(
            await self.session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.org_id == self.org_id,
                    Notification.user_id == self.ctx.user.id,
                    Notification.read_at.is_(None),
                    Notification.visible_at <= func.now(),
                )
            )
            or 0
        )

    async def set_read(self, notification_id: uuid.UUID, read: bool) -> dict[str, Any]:
        """Reversible toggle — marking read destroys nothing (docs/UX.md)."""
        row = (await self.session.execute(
            self._visible().where(Notification.id == notification_id)
        )).first()
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        notification = row[0]
        notification.read_at = datetime.now(UTC) if read else None
        await self.session.flush()
        return self._read(row)

    async def mark_all_read(self) -> int:
        result = await self.session.execute(
            update(Notification)
            .where(
                Notification.org_id == self.org_id,
                Notification.user_id == self.ctx.user.id,
                Notification.read_at.is_(None),
                Notification.visible_at <= func.now(),
            )
            .values(read_at=datetime.now(UTC))
        )
        await self.session.flush()
        return int(result.rowcount or 0)

    # ----------------------------------------------------------------- #
    # Activity feed — the event log for one record, recipient-independent
    # ----------------------------------------------------------------- #
    async def activity(
        self, entity_type: str, entity_id: uuid.UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        rows = (
            await self.session.execute(
                select(NotificationEvent, User.full_name, User.email)
                .outerjoin(User, User.id == NotificationEvent.actor_user_id)
                .where(
                    NotificationEvent.org_id == self.org_id,
                    NotificationEvent.entity_type == entity_type,
                    NotificationEvent.entity_id == entity_id,
                )
                .order_by(NotificationEvent.created_at.desc())
                .limit(limit)
            )
        ).all()
        return [
            {
                "id": event.id,
                "event_type": event.event_type,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "actor_name": _display_name(full_name, email),
                "payload": event.payload,
                "created_at": event.created_at,
            }
            for event, full_name, email in rows
        ]

    # ----------------------------------------------------------------- #
    # Watchers — tri-state: watch / mute / auto (no row)
    # ----------------------------------------------------------------- #
    async def watch_state(self, entity_type: str, entity_id: uuid.UUID) -> bool | None:
        row = await self.session.scalar(
            select(NotificationWatcher).where(
                NotificationWatcher.org_id == self.org_id,
                NotificationWatcher.user_id == self.ctx.user.id,
                NotificationWatcher.entity_type == entity_type,
                NotificationWatcher.entity_id == entity_id,
            )
        )
        if row is None:
            return None
        return not row.muted

    async def set_watch(
        self, entity_type: str, entity_id: uuid.UUID, watching: bool | None
    ) -> bool | None:
        if entity_type not in ENTITY_TYPES:
            raise AppError("validation", "errors.validation", status_code=422)
        row = await self.session.scalar(
            select(NotificationWatcher).where(
                NotificationWatcher.org_id == self.org_id,
                NotificationWatcher.user_id == self.ctx.user.id,
                NotificationWatcher.entity_type == entity_type,
                NotificationWatcher.entity_id == entity_id,
            )
        )
        if watching is None:  # back to the default fan-out
            if row is not None:
                await self.session.delete(row)
                await self.session.flush()
            return None
        if row is None:
            self.session.add(
                NotificationWatcher(
                    org_id=self.org_id,
                    user_id=self.ctx.user.id,
                    entity_type=entity_type,
                    entity_id=entity_id,
                    muted=not watching,
                )
            )
        else:
            row.muted = not watching
        await self.session.flush()
        return watching


# --------------------------------------------------------------------------- #
# Subscription
# --------------------------------------------------------------------------- #
def make_handler(event_type: str):  # noqa: ANN201 - returns an EventHandler
    """One generic handler per event: locate the subject, hand the rest to the fan-out."""
    entity_type = ENTITY_FOR_EVENT[event_type]
    id_key = ENTITY_ID_KEY[event_type]

    async def handler(ctx: EmitContext, payload: dict[str, Any]) -> None:
        entity_id = _as_uuid(payload.get(id_key))
        if entity_id is None:
            return
        await NotificationService(ctx).ingest(event_type, entity_type, entity_id, payload)

    handler.__name__ = f"notify_on_{event_type.replace('.', '_')}"
    return handler
