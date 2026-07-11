"""``ActivityService`` — record and read a record's paper trail (issue #67).

Tenant-scoped, like every service. ``record`` writes one ``activity_log`` row in the caller's
request transaction, so an activity line and the change it describes commit atomically (or roll
back together). ``feed`` reads the trail for one entity, resolving the actor the way the tasks
module does (issue #64): the live account wins while it exists, and a snapshot taken at write
time covers a since-deleted user so their work never silently becomes "the system".
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from sqlalchemy import select

from app.core.activity.models import ActivityLog
from app.core.auth.models import User
from app.core.events import EmitContext

# Actions this core capability writes. ``created``/``updated`` are universal; a module that
# wants a richer verb (a status move) records it under its own ``activity.action.*`` key.
ACTION_CREATED = "created"
ACTION_UPDATED = "updated"


def _display_name(full_name: str | None, email: str | None) -> str | None:
    """How the UI names a person; ``None`` means the system acted (mirrors notifications)."""
    return (full_name or email) if email is not None else None


def jsonable(value: Any) -> Any:
    """Coerce a column value into something JSONB (and an i18n renderer) can hold."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    return str(value)


def diff(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """``{field: {"from": old, "to": new}}`` for every field whose value actually changed.

    Both sides are coerced with :func:`jsonable` before comparison, so an ``enum`` and its
    stored string, or a ``date`` and its ISO form, don't read as a spurious change.
    """
    changes: dict[str, dict[str, Any]] = {}
    for field in before.keys() | after.keys():
        old, new = jsonable(before.get(field)), jsonable(after.get(field))
        if old != new:
            changes[field] = {"from": old, "to": new}
    return changes


def snapshot(obj: object, fields: Iterable[str]) -> dict[str, Any]:
    """The tracked fields' current values, JSON-coerced — take one before a write and one after."""
    return {field: jsonable(getattr(obj, field, None)) for field in fields}


class ActivityService:
    def __init__(self, ctx: EmitContext) -> None:
        self.ctx = ctx

    async def record(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Append one trail entry in the current transaction. No-op flush is the caller's."""
        actor = self.ctx.user
        row = ActivityLog(
            org_id=self.ctx.org.id,
            entity_type=entity_type,
            entity_id=entity_id,
            actor_user_id=actor.id if actor else None,
            actor_name=_display_name(actor.full_name, actor.email) if actor else None,
            action=action,
            payload=payload or {},
        )
        self.ctx.session.add(row)

    async def record_created(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record the birth of a record."""
        await self.record(entity_type, entity_id, ACTION_CREATED, payload)

    async def record_update(
        self,
        entity_type: str,
        entity_id: uuid.UUID,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> None:
        """Record an ``updated`` entry iff any tracked field changed — never an empty edit."""
        changes = diff(before, after)
        if changes:
            await self.record(entity_type, entity_id, ACTION_UPDATED, {"changes": changes})

    async def feed(
        self, entity_type: str, entity_id: uuid.UUID, limit: int = 20
    ) -> list[dict[str, Any]]:
        """The trail for one entity, newest first, with the actor resolved per issue #64."""
        rows = (
            await self.ctx.session.execute(
                select(ActivityLog, User.full_name, User.email)
                .outerjoin(User, User.id == ActivityLog.actor_user_id)
                .where(
                    ActivityLog.org_id == self.ctx.org.id,
                    ActivityLog.entity_type == entity_type,
                    ActivityLog.entity_id == entity_id,
                )
                .order_by(ActivityLog.created_at.desc())
                .limit(limit)
            )
        ).all()
        items: list[dict[str, Any]] = []
        for row, full_name, email in rows:
            if email is not None:  # the account still exists — the live name wins
                actor_name, actor_deleted = _display_name(full_name, email), False
            else:  # SET NULL fired (or never had an actor): fall back to the snapshot
                actor_name = row.actor_name
                actor_deleted = row.actor_name is not None
            items.append(
                {
                    "id": row.id,
                    "entity_type": row.entity_type,
                    "entity_id": row.entity_id,
                    "action": row.action,
                    "actor_name": actor_name,
                    "actor_deleted": actor_deleted,
                    "payload": row.payload,
                    "created_at": row.created_at,
                }
            )
        return items
