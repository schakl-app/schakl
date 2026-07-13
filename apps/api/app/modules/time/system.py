"""Published time-entry writes for other modules (CLAUDE.md §6).

The boundary another module crosses instead of touching ``TimeEntry`` internals — exactly
like ``interactions.system`` is for the gmail feed and ``tasks.system`` for automation.
First consumer: the interaction form's "Voeg aan mijn uren toe" checkbox (#175), which logs
a linked time entry in the same transaction as the interaction it came from. Tenant-scoped
through the context's session; the *caller* holds the permission check (``time.entry.write``)
— this helper only writes what it is handed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import select

from app.core.events import EmitContext
from app.errors import AppError
from app.modules.time.models import TimeEntry, TimeEntryType


async def active_type_key(ctx: EmitContext, key: str | None) -> str | None:
    """``key`` when it names one of the org's *active* entry types, else ``None`` — how a
    caller types an entry after its own vocabulary (an interaction kind, #175/#176) without
    reading this module's tables."""
    if key is None:
        return None
    hit = await ctx.session.scalar(
        select(TimeEntryType.id).where(
            TimeEntryType.org_id == ctx.org.id,
            TimeEntryType.key == key,
            TimeEntryType.active.is_(True),
        )
    )
    return key if hit is not None else None


async def record_entry(
    ctx: EmitContext,
    *,
    user_id: uuid.UUID,
    started_at: datetime,
    ended_at: datetime,
    company_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    task_id: uuid.UUID | None = None,
    description: str | None = None,
    entry_type_key: str | None = None,
    interaction_id: uuid.UUID | None = None,
) -> TimeEntry:
    """Insert one stopped entry. Times follow the time module's own convention
    (wall-clock-as-UTC); an end at or before the start rolls forward a day, like the
    manual-entry path. A zero-length span is a validation error, not a stored zero."""
    if ended_at <= started_at:
        ended_at += timedelta(days=1)
    minutes = max(0, round((ended_at - started_at).total_seconds() / 60))
    if minutes == 0:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"ended_at": "errors.validation"},
        )
    row = TimeEntry(
        org_id=ctx.org.id,
        user_id=user_id,
        started_at=started_at,
        ended_at=ended_at,
        minutes=minutes,
        company_id=company_id,
        project_id=project_id,
        task_id=task_id,
        description=description,
        entry_type_key=entry_type_key,
        interaction_id=interaction_id,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    return row
