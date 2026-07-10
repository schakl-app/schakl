"""Daily deadline reminders: ``task.due_soon`` and ``task.overdue`` (issue #16).

The tasks module owns the deadlines, so it owns the reminder. It only *emits*; who hears about
it, and when, is the notifications module's business (CLAUDE.md §6).

Two things make this idempotent rather than a daily nag:

* every emit carries a **dedup key** that includes the due date, so a task announces itself
  once per deadline — and re-announces if somebody reschedules it;
* ``due_soon`` fires on exactly the day that matches the *assignee's own* threshold, which the
  cron reads through the one published interface the notifications module exposes
  (``prefs.due_soon_thresholds``) rather than by reaching into its tables.

ARQ cron fires in UTC; ``today_local`` reasons in ``Europe/Amsterdam`` so "due today" means
what a person in Amsterdam means by it.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext, emit
from app.core.models import Org
from app.modules.tasks.models import Task, TaskStatus
from app.modules.tasks.recurrence import today_local


async def _emit(ctx: SystemContext, event: str, task: Task, dedup: str, params: dict) -> None:
    payload = {
        "task_id": task.id,
        "title": task.title,
        "_recipients": [task.assignee_user_id],
        "_dedup_key": dedup,
    }
    payload.update(params)
    await emit(event, ctx, payload)


async def remind_for_org(org: Org, session: AsyncSession, *, today: date | None = None) -> int:
    """Announce every deadline this org's people should know about today.

    Returns the number of *candidates announced*, not notifications delivered: the cron
    re-announces a still-overdue task on every tick and the notifications module drops the
    repeat on its dedup key. Emitters cannot see that decision, which is the point.
    """
    # Deferred, like every other cross-module call here: we ask notifications what "soon"
    # means to each person, through its published helper — never by reading its tables (§6).
    from app.modules.notifications.prefs import due_soon_thresholds

    today = today or today_local()
    thresholds = await due_soon_thresholds(session, org.id)
    horizon = today + timedelta(days=max(thresholds.values()))

    tasks = (
        await session.execute(
            select(Task).where(
                Task.org_id == org.id,
                Task.assignee_user_id.is_not(None),
                Task.status != TaskStatus.DONE.value,
                Task.due_date.is_not(None),
                # Everything already late, plus everything anyone could call "soon".
                Task.due_date <= horizon,
            )
        )
    ).scalars().all()

    ctx = SystemContext(org=org, session=session)
    emitted = 0
    for task in tasks:
        due: date = task.due_date  # type: ignore[assignment]
        assignee: uuid.UUID = task.assignee_user_id  # type: ignore[assignment]
        if due < today:
            await _emit(
                ctx, "task.overdue", task,
                f"task.overdue:{task.id}:{due.isoformat()}",
                {"due_date": due},
            )
            emitted += 1
            continue
        days_left = (due - today).days
        if days_left == thresholds.get(assignee, thresholds[None]):
            await _emit(
                ctx, "task.due_soon", task,
                f"task.due_soon:{task.id}:{due.isoformat()}",
                {"due_date": due, "days_left": days_left},
            )
            emitted += 1
    return emitted


async def send_task_reminders(ctx: dict) -> int:
    """ARQ cron entry point: deadline reminders for every org."""
    from app.core.jobs import run_per_org

    total = 0

    async def _per_org(org: Org, session: AsyncSession) -> None:
        nonlocal total
        total += await remind_for_org(org, session)

    await run_per_org(_per_org)
    return total
