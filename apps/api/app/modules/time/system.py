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
from typing import Any

from sqlalchemy import func, select

from app.core.events import EmitContext
from app.errors import AppError
from app.modules.time.models import DEFAULT_ENTRY_TYPES, TimeEntry, TimeEntryType


async def ensure_type_for_kind(
    ctx: EmitContext, key: str | None, label_i18n: dict[str, Any] | None = None
) -> str | None:
    """The org's time-entry type matching an interaction kind, provisioned on first use (#182).

    A time entry logged from a call/meeting should carry that kind as its *type* (#175/#176),
    but the two lists are independent and time-entry types seed only ``work``/``email`` — so a
    call mapped to nothing and came through untyped. This mirrors the kind into a time-entry
    type the first time one is logged, keeping the lists in sync without a shared table:

    - an **active** matching type already exists → use it;
    - a **deactivated** one exists → respect the admin's choice, leave the entry untyped;
    - none exists → create one from the interaction kind's own ``label_i18n`` (so it reads
      identically in Uren-typen and the report), appended after the current types.

    Provisioning is a side effect of a write the caller already holds ``time.entry.write`` for
    (like an activity record), **not** a ``time.entry_type.manage`` action — a member logging a
    call must still get it typed, and members don't manage the catalog.
    """
    if key is None:
        return None
    existing = await ctx.session.scalar(
        select(TimeEntryType).where(
            TimeEntryType.org_id == ctx.org.id, TimeEntryType.key == key
        )
    )
    if existing is not None:
        return key if existing.active else None

    # Seed the defaults into a still-empty catalog first: the lazy ``count == 0`` seed would
    # never fire again once the kind row below is inserted, stranding an org without ``work``.
    count = int(
        await ctx.session.scalar(
            select(func.count())
            .select_from(TimeEntryType)
            .where(TimeEntryType.org_id == ctx.org.id)
        )
        or 0
    )
    if count == 0:
        for spec in DEFAULT_ENTRY_TYPES:
            ctx.session.add(TimeEntryType(org_id=ctx.org.id, **spec))
        await ctx.session.flush()
        if key in {spec["key"] for spec in DEFAULT_ENTRY_TYPES}:
            return key

    next_position = int(
        await ctx.session.scalar(
            select(func.coalesce(func.max(TimeEntryType.position), 0)).where(
                TimeEntryType.org_id == ctx.org.id
            )
        )
        or 0
    )
    ctx.session.add(
        TimeEntryType(
            org_id=ctx.org.id,
            key=key,
            label_i18n=dict(label_i18n or {}),
            position=next_position + 10,
        )
    )
    await ctx.session.flush()
    return key


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
