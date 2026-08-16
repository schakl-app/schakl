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


async def resolve_billable(
    ctx: EmitContext, billable: bool | None, project_id: uuid.UUID | None
) -> bool:
    """Resolve a new entry's ``billable`` (issue #284), for every path that writes one.

    Stated by the caller, it stands; left out, the project answers — and a project a
    subscription covers answers *no*, because the retainer already pays for that work. It lives
    here rather than only in ``TimeService`` because a ride-along entry (#175's contact moment,
    #314's finished task) is the same entry: a second copy of this rule is how one write path
    quietly starts billing a retainer client for hours the retainer already covers.
    """
    if billable is not None:
        return billable
    if project_id is None:
        return True
    from app.modules.projects.service import ProjectService

    return await ProjectService(ctx).billable_default(project_id)


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
    billable: bool | None = None,
) -> TimeEntry:
    """Insert one stopped entry. Times follow the time module's own convention
    (wall-clock-as-UTC); an end at or before the start rolls forward a day, like the
    manual-entry path. A zero-length span is a validation error, not a stored zero.
    ``billable`` left out defers to the project, exactly as the entry form does (#284)."""
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
        billable=await resolve_billable(ctx, billable, project_id),
    )
    ctx.session.add(row)
    await ctx.session.flush()
    return row


async def revise_entry(
    ctx: EmitContext,
    entry: TimeEntry,
    *,
    started_at: datetime | None = None,
    minutes: int | None = None,
    company_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    description: str | None = None,
    billable: bool | None = None,
    touch: frozenset[str] = frozenset(),
) -> TimeEntry:
    """Correct one stopped entry on behalf of whoever owns it (#382, the Timeon sync).

    The counterpart to :func:`record_entry`, and it exists for the same reason: a mirror of an
    external timesheet has to be able to carry a *correction* across, and ``TimeService.update``
    cannot — it resolves the row against ``ctx.user``, refuses an approved entry unless the
    caller may approve, and clears the owner's draft for that day. None of those is wrong for a
    person editing their own hours and all three are wrong for a sync acting for somebody else.

    ``touch`` names the fields the caller means to write, so ``None`` keeps its ordinary meaning
    — a company or project being *cleared* — instead of being indistinguishable from "leave it
    alone" (§18's rule: absent means leave alone, explicit null means clear). Anything not named
    is untouched.

    ``ended_at`` is kept consistent with the new duration, because everything downstream — the
    day view, the calendar, capacity — positions the block by it and a stale end is an entry that
    renders at the wrong length while reporting the right number.
    """
    values: dict[str, Any] = {}
    if "started_at" in touch and started_at is not None:
        values["started_at"] = started_at
    if "minutes" in touch and minutes is not None:
        values["minutes"] = max(0, int(minutes))
    if "company_id" in touch:
        values["company_id"] = company_id
    if "project_id" in touch:
        values["project_id"] = project_id
    if "description" in touch:
        values["description"] = description
    if "billable" in touch and billable is not None:
        values["billable"] = billable
    for key, value in values.items():
        setattr(entry, key, value)
    if entry.ended_at is not None or "minutes" in values or "started_at" in values:
        entry.ended_at = entry.started_at + timedelta(
            minutes=int(entry.minutes) + int(entry.break_minutes or 0)
        )
    await ctx.session.flush()
    return entry


async def remove_entry(ctx: EmitContext, entry: TimeEntry) -> None:
    """Delete one entry on behalf of its owner.

    Named rather than left as ``session.delete`` so the boundary is a function another module
    calls (§6) and so there is one place to state the rule the callers depend on: this deletes a
    *record of work*, so whoever calls it owes the decision — the Timeon sync refuses on an
    invoiced or approved entry before it ever gets here.
    """
    await ctx.session.delete(entry)
    await ctx.session.flush()
