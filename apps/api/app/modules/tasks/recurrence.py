"""Recurring tasks (CLAUDE.md §6 automation).

Deliberately simple: a chain has exactly one carrier — the task holding ``recurrence``.
Two disjoint modes:

- ``after_completion``: when the carrier is completed, the service calls :func:`spawn_next`;
  the clone becomes the new carrier.
- ``schedule``: a daily cron spawns when ``recurrence_next_run`` has arrived, regardless of
  completion, and advances ``next_run`` onto the clone.

Functions take ``(session, org_id, …)`` — no ``RequestContext`` — so the ARQ worker can call
them with :func:`app.core.jobs.run_per_org`. Every query filters ``org_id`` explicitly
(Golden Rule 1); RLS backs it up.
"""

from __future__ import annotations

import calendar
import uuid
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tasks.models import (
    RecurrenceFreq,
    RecurrenceMode,
    Task,
    TaskActivity,
    TaskChecklist,
    TaskChecklistItem,
    TaskLabelLink,
    TaskStatus,
)

_TZ = ZoneInfo("Europe/Amsterdam")


def today_local() -> date:
    """Today in the platform's default timezone (cron fires in UTC)."""
    return datetime.now(_TZ).date()


def advance(d: date, freq: str, interval: int) -> date:
    """``d`` plus ``interval`` recurrence steps; month arithmetic clamps the day-of-month."""
    if freq == RecurrenceFreq.DAILY.value:
        return d + timedelta(days=interval)
    if freq == RecurrenceFreq.WEEKLY.value:
        return d + timedelta(weeks=interval)

    months = {
        RecurrenceFreq.MONTHLY.value: interval,
        RecurrenceFreq.QUARTERLY.value: 3 * interval,
        RecurrenceFreq.YEARLY.value: 12 * interval,
    }[freq]
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    day = min(d.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def next_due(due_date: date | None, rec: dict) -> date:
    """The next occurrence's date: keeps the cadence anchor, but is never in the past."""
    freq, interval = rec["freq"], rec.get("interval", 1)
    due = advance(due_date or today_local(), freq, interval)
    while due <= today_local():
        due = advance(due, freq, interval)
    return due


def compute_next_run(rec: dict | None, due_date: date | None) -> date | None:
    """When the daily cron should next materialize an occurrence (schedule mode only)."""
    if rec and rec.get("mode") == RecurrenceMode.SCHEDULE.value:
        return next_due(due_date, rec)
    return None


async def _max_position(session: AsyncSession, org_id: uuid.UUID) -> float:
    result = await session.scalar(
        select(func.max(Task.position)).where(Task.org_id == org_id)
    )
    return float(result or 0.0)


async def spawn_next(
    session: AsyncSession,
    org_id: uuid.UUID,
    task: Task,
    *,
    actor_user_id: uuid.UUID | None,
    actor_name: str | None = None,
) -> Task:
    """Clone the carrier into the next occurrence and hand it the recurrence.

    Copies title/description/priority/company/project/assignee, the label links, and the
    checklists with every item reset to not-done. The source task stops recurring.

    ``actor_name`` snapshots whoever completed the carrier, so the spawned task's first activity
    line keeps naming them after their account is deleted. The cron passes neither and is
    genuinely the system — that is the distinction the snapshot exists to preserve (issue #64).
    """
    rec = dict(task.recurrence or {})
    due = next_due(task.due_date, rec)

    clone = Task(
        org_id=org_id,
        company_id=task.company_id,
        project_id=task.project_id,
        assignee_user_id=task.assignee_user_id,
        title=task.title,
        description=task.description,
        status=TaskStatus.OPEN.value,
        priority=task.priority,
        due_date=due,
        allocated_minutes=task.allocated_minutes,
        position=await _max_position(session, org_id) + 1024.0,
        recurrence=rec,
        recurrence_next_run=(
            due if rec.get("mode") == RecurrenceMode.SCHEDULE.value else None
        ),
    )
    session.add(clone)
    await session.flush()

    links = (
        await session.execute(
            select(TaskLabelLink).where(
                TaskLabelLink.org_id == org_id, TaskLabelLink.task_id == task.id
            )
        )
    ).scalars().all()
    for link in links:
        session.add(TaskLabelLink(org_id=org_id, task_id=clone.id, label_id=link.label_id))

    checklists = (
        await session.execute(
            select(TaskChecklist).where(
                TaskChecklist.org_id == org_id, TaskChecklist.task_id == task.id
            )
        )
    ).scalars().all()
    for checklist in checklists:
        new_checklist = TaskChecklist(
            org_id=org_id,
            task_id=clone.id,
            title=checklist.title,
            position=checklist.position,
        )
        session.add(new_checklist)
        await session.flush()
        items = (
            await session.execute(
                select(TaskChecklistItem).where(
                    TaskChecklistItem.org_id == org_id,
                    TaskChecklistItem.checklist_id == checklist.id,
                )
            )
        ).scalars().all()
        for item in items:
            session.add(
                TaskChecklistItem(
                    org_id=org_id,
                    checklist_id=new_checklist.id,
                    title=item.title,
                    done=False,
                    position=item.position,
                )
            )

    task.recurrence = None
    task.recurrence_next_run = None
    session.add(
        TaskActivity(
            org_id=org_id,
            task_id=clone.id,
            actor_user_id=actor_user_id,
            actor_name=actor_name,
            action="recurrence_spawned",
            payload={"source_task_id": str(task.id)},
        )
    )
    await session.flush()
    return clone


async def spawn_due_for_org(session: AsyncSession, org_id: uuid.UUID) -> int:
    """Spawn every schedule-mode occurrence whose ``next_run`` has arrived. Returns count."""
    tasks = (
        await session.execute(
            select(Task).where(
                Task.org_id == org_id,
                Task.recurrence_next_run.is_not(None),
                Task.recurrence_next_run <= today_local(),
            )
        )
    ).scalars().all()
    spawned = 0
    for task in tasks:
        rec = task.recurrence or {}
        if rec.get("mode") != RecurrenceMode.SCHEDULE.value:
            continue
        await spawn_next(session, org_id, task, actor_user_id=None)
        spawned += 1
    return spawned


async def spawn_scheduled_recurrences(ctx: dict) -> int:
    """ARQ cron entry point: materialize scheduled recurrences for every org."""
    from app.core.jobs import run_per_org

    total = 0

    async def _per_org(org, session) -> None:
        nonlocal total
        total += await spawn_due_for_org(session, org.id)

    await run_per_org(_per_org)
    return total
