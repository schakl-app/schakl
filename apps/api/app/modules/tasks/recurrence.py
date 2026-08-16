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
import logging
import uuid
from datetime import date, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import org_today
from app.modules.tasks.models import (
    RecurrenceFreq,
    RecurrenceMode,
    Task,
    TaskActivity,
    TaskAssignee,
    TaskChecklist,
    TaskChecklistItem,
    TaskLabelLink,
    TaskLink,
)
from app.modules.tasks.schemas import ScheduleCreate
from app.modules.tasks.statuses import default_key, load_statuses

logger = logging.getLogger("schakl.tasks.recurrence")

# No module-level zone: "today" is a question about *whose* calendar, and the answer is the
# org's `org_settings.timezone` (CLAUDE.md §8). Every entry point below therefore takes the day
# it should reason about, resolved by its caller through `app.core.timezone.org_today` — a
# hardcoded city silently gave a tenant in Lisbon or Warsaw somebody else's midnight.


def _clamped(year: int, month: int, day: int) -> date:
    """``day`` of that month, or its last day — 31 January stepped a month is 28/29 February."""
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def snap(d: date, freq: str, *, on_weekday=None, on_day=None, on_month=None) -> date:
    """``d`` moved onto its rule's anchor **within its own period** — never into the next one.

    The counterpart to :func:`advance`: that one steps a period, this one places a date inside
    the period it is already in. Splitting them is what makes "elke maand op dag 20", written on
    the 15th, resolve to *this* month's 20th rather than next month's — a rule the user would
    otherwise have to wait a month to see obeyed.
    """
    if freq == RecurrenceFreq.WEEKLY.value and on_weekday is not None:
        return d + timedelta(days=on_weekday - d.weekday())
    if freq == RecurrenceFreq.YEARLY.value and on_day is not None and on_month is not None:
        return _clamped(d.year, on_month, on_day)
    if (
        freq in (RecurrenceFreq.MONTHLY.value, RecurrenceFreq.QUARTERLY.value)
        and on_day is not None
    ):
        return _clamped(d.year, d.month, on_day)
    return d


def advance(
    d: date, freq: str, interval: int, *, on_weekday=None, on_day=None, on_month=None
) -> date:
    """``d`` plus ``interval`` recurrence steps, landed on the rule's anchor where it has one.

    Always **strictly after** ``d``, anchors included — :func:`next_due` loops on this, so a step
    that could return its own input would not terminate. It holds by construction: a weekly snap
    moves at most six days back inside a week that started ``7·interval`` days later, and a month
    step lands in a later month, every day of which is later than every day of ``d``'s.
    """
    if freq == RecurrenceFreq.DAILY.value:
        return d + timedelta(days=interval)
    if freq == RecurrenceFreq.WEEKLY.value:
        stepped = d + timedelta(weeks=interval)
        return snap(stepped, freq, on_weekday=on_weekday)

    months = {
        RecurrenceFreq.MONTHLY.value: interval,
        RecurrenceFreq.QUARTERLY.value: 3 * interval,
        RecurrenceFreq.YEARLY.value: 12 * interval,
    }[freq]
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    if freq == RecurrenceFreq.YEARLY.value and on_month is not None and on_day is not None:
        # A yearly anchor names the whole date, so the stepped month is only there to carry the
        # year: "elk jaar op 15 maart" is 15 March, whatever month the rule was written in.
        return _clamped(year, on_month, on_day)
    return _clamped(year, month, on_day if on_day is not None else d.day)


def _anchors(rec: dict) -> dict:
    """The rule's anchor fields, absent-as-``None`` — a rule stored before #335 has none."""
    return {
        "on_weekday": rec.get("on_weekday"),
        "on_day": rec.get("on_day"),
        "on_month": rec.get("on_month"),
    }


def next_due(due_date: date | None, rec: dict, *, today: date) -> date:
    """The next occurrence's date: keeps the cadence anchor, but is never in the past.

    ``today`` is the org's local day (`app.core.timezone.org_today`), passed in rather than read
    here: "not in the past" is a local-calendar claim, and a task due today must not be advanced
    a week because the server's UTC clock has already rolled over.

    With no due date the rhythm has nothing to hang off, so an **anchored** rule looks inside
    today's own period first (:func:`snap`) before stepping — otherwise "op dag 20", written on
    the 15th, would skip a month for no reason a user could see.
    """
    freq, interval = rec["freq"], rec.get("interval", 1)
    anchors = _anchors(rec)
    if due_date is None and any(v is not None for v in anchors.values()):
        candidate = snap(today, freq, **anchors)
        if candidate > today:
            return candidate
    due = advance(due_date or today, freq, interval, **anchors)
    while due <= today:
        due = advance(due, freq, interval, **anchors)
    return due


def upcoming(due_date: date | None, rec: dict, *, today: date, count: int = 3) -> list[date]:
    """``next_due`` and the ``count - 1`` occurrences after it — the preview's rhythm check.

    One date proves nothing about a rhythm: "13 sep" is equally consistent with monthly, weekly
    and a one-off, and the whole point of the preview is that the user can tell which they wrote.
    """
    freq, interval = rec["freq"], rec.get("interval", 1)
    anchors = _anchors(rec)
    dates = [next_due(due_date, rec, today=today)]
    while len(dates) < max(1, count):
        dates.append(advance(dates[-1], freq, interval, **anchors))
    return dates


def compute_next_run(rec: dict | None, due_date: date | None, *, today: date) -> date | None:
    """When the daily cron should next materialize an occurrence (schedule mode only)."""
    if rec and rec.get("mode") == RecurrenceMode.SCHEDULE.value:
        return next_due(due_date, rec, today=today)
    return None


async def _max_position(session: AsyncSession, org_id: uuid.UUID) -> float:
    result = await session.scalar(
        select(func.max(Task.position)).where(Task.org_id == org_id)
    )
    return float(result or 0.0)


#: What a spawn carries to the next occurrence. Enumerated rather than implied, because F4 (#335)
#: was three fields nobody had *decided* about: a client-visible recurring task spawned an
#: internal clone, a task assigned to a client contact spawned unassigned, and the briefing links
#: a recurring job needs were lost every cycle. ``tests/test_task_recurrence.py`` sweeps
#: ``Task.__table__.columns`` against these two sets, so adding a column to ``tasks`` without
#: saying which side it falls on is a build break rather than a silent drop next release.
COPIED_FIELDS = frozenset(
    {
        "title",
        "description",
        "priority",
        "company_id",
        "project_id",
        "assignee_user_id",
        # #273's assignee is an assignee: "waiting on the client to send the materials" is as
        # true of December's occurrence as of November's.
        "assignee_contact_id",
        "allocated_minutes",
        # The close policy is a property of the work (#157 extended), and so is who may read it.
        "requires_interaction",
        "visible_to_client",
        "recurrence",
        # Copied *because* ``title`` is (#350, #369). ``unnamed`` is a fact about the title, not
        # about the occurrence: it says the stored string is a placeholder nobody typed, written
        # in whichever locale the creator happened to be using. Leaving it behind while carrying
        # the title forward would hand the clone that placeholder **as if it were a name** —
        # precisely the failure the flag exists to prevent, reintroduced monthly by a cron. The
        # "a fresh occurrence is a fresh record" argument below does not reach it: the clone did
        # not acquire a name by being created, and the moment somebody types one the service
        # clears the flag on the clone alone.
        "unnamed",
    }
)

#: Deliberately *not* carried, each for its own reason — a fresh occurrence is a fresh record.
NOT_COPIED_FIELDS: dict[str, str] = {
    "id": "a new row",
    "org_id": "the tenant, set on create",
    "created_at": "when this occurrence began, not its predecessor",
    "updated_at": "same",
    "status": "starts in the org's default status (#62), never inheriting a finished one",
    "due_date": "computed from the rule (next_due)",
    "recurrence_next_run": "computed from the rule",
    "position": "appended to the board rather than sharing the carrier's slot",
    "completed_at": "nothing has been completed yet",
    "closing_interaction_id": "last month's phone call cannot close this month's work (#157)",
    "ai_status": "a run that touched the carrier says nothing about the clone (#327)",
    "ai_status_at": "same",
}


async def spawn_next(
    session: AsyncSession,
    org_id: uuid.UUID,
    task: Task,
    *,
    actor_user_id: uuid.UUID | None,
    actor_name: str | None = None,
    today: date | None = None,
    ctx: Any | None = None,
) -> Task:
    """Clone the carrier into the next occurrence and hand it the recurrence.

    Copies :data:`COPIED_FIELDS`, the label links, the task links, and the checklists with every
    item reset to not-done. Attachments and planned blocks stay behind on purpose (they are
    *this* occurrence's output and *this* occurrence's calendar); the editor says so in words
    rather than leaving it to be discovered. The source task stops recurring.

    ``actor_name`` snapshots whoever completed the carrier, so the spawned task's first activity
    line keeps naming them after their account is deleted. The cron passes neither and is
    genuinely the system — that is the distinction the snapshot exists to preserve (issue #64).

    ``ctx`` — a request context, or the cron's ``system_context`` — is what lets a rule carrying
    a ``plan`` book the occurrence onto a calendar (#335). It goes through ``TaskScheduleService``
    rather than inserting a row, so the Google mirror and the "taak ingepland" notification fire
    exactly as they do for a hand-planned block (#188's one-emit-site rule). Without a context
    the clone is still created; only the block is skipped.
    """
    rec = dict(task.recurrence or {})
    # The org's local day, resolved by the caller when there is a batch of these — the cron
    # spawns many in one sweep and one lookup per task would be an N+1 (docs/PERFORMANCE.md).
    today_local = today if today is not None else await org_today(session, org_id)
    due = next_due(task.due_date, rec, today=today_local)

    # A fresh occurrence starts in the org's default status (issue #62), not a hardcoded "open".
    default_status = default_key(await load_statuses(session, org_id))

    clone = Task(
        org_id=org_id,
        status=default_status,
        due_date=due,
        position=await _max_position(session, org_id) + 1024.0,
        recurrence_next_run=(
            due if rec.get("mode") == RecurrenceMode.SCHEDULE.value else None
        ),
        # Everything the rule decided carries over, from the one enumerated set — so a column
        # added to ``tasks`` cannot quietly stop repeating (see ``COPIED_FIELDS``).
        **{field: getattr(task, field) for field in COPIED_FIELDS if field != "recurrence"},
        recurrence=rec,
    )
    session.add(clone)
    await session.flush()

    # The briefing and design URLs a recurring job needs are *definition*, not output: they
    # described the work before this occurrence existed and describe the next one too (#335 F4).
    task_links = (
        await session.execute(
            select(TaskLink).where(TaskLink.org_id == org_id, TaskLink.task_id == task.id)
        )
    ).scalars().all()
    for task_link in task_links:
        session.add(
            TaskLink(org_id=org_id, task_id=clone.id, url=task_link.url, title=task_link.title)
        )

    # The people who work it, all of them (#375). ``assignee_user_id`` rides in COPIED_FIELDS and
    # would leave a shared recurring job repeating onto one person — the roster is definition in
    # exactly the way the labels above are, not this occurrence's output.
    for assignee in (
        await session.execute(
            select(TaskAssignee).where(
                TaskAssignee.org_id == org_id, TaskAssignee.task_id == task.id
            )
        )
    ).scalars().all():
        session.add(
            TaskAssignee(
                org_id=org_id,
                task_id=clone.id,
                user_id=assignee.user_id,
                is_primary=assignee.is_primary,
            )
        )

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
    # The hand-off, said on **both** sides (#335 F5). The clone has always announced where it came
    # from; the carrier said nothing at all, so completing a recurring task looked exactly like
    # completing an ordinary one and the next occurrence existed with nobody told.
    session.add(
        TaskActivity(
            org_id=org_id,
            task_id=task.id,
            actor_user_id=actor_user_id,
            actor_name=actor_name,
            action="recurrence_spawned_next",
            payload={"next_task_id": str(clone.id), "due_date": due.isoformat()},
        )
    )
    await session.flush()
    if ctx is not None:
        await plan_occurrence(ctx, clone, rec, today=today_local)
    return clone


async def plan_occurrence(ctx: Any, clone: Task, rec: dict, *, today: date) -> None:
    """Book the freshly spawned occurrence onto a calendar, when the rule says to (#335 phase 5).

    Through ``TaskScheduleService.create`` rather than an insert: that is the module's one emit
    site (#188), so the Google mirror and the scheduled person's notification ride along, and the
    org's wall clock resolves per occurrence — a 09:00 block is 09:00 on both sides of a DST
    switch. Three refusals, and each one is a way this could otherwise do harm quietly:

    * **no plan on the rule** — nothing to do;
    * **no one to plan for** — an unassigned occurrence with no explicit person names nobody's
      calendar, and inventing one would put a colleague's day in someone else's rule;
    * **a day already past** — a late completion in April must not book a block for 1 March.
      ``next_due`` already guarantees a future date, so this only fires on a rule whose plan was
      added to a carrier whose due date is behind it.

    A failure here is not allowed to lose the occurrence: the clone is the point and the block is
    the convenience, so a refusal (a person who left, a licence lapse) is swallowed rather than
    rolling back a spawn the cron will never retry — **inside a SAVEPOINT**, because catching an
    error without one leaves the session poisoned for everything after it (§18).
    """
    plan = rec.get("plan")
    if not plan:
        return
    if clone.due_date is None or clone.due_date < today:
        return
    user_id = plan.get("user_id") or clone.assignee_user_id
    if user_id is None:
        return
    from app.modules.tasks.scheduling import TaskScheduleService

    try:
        async with ctx.session.begin_nested():
            await TaskScheduleService(ctx).create(
                ScheduleCreate(
                    task_id=clone.id,
                    user_id=uuid.UUID(str(user_id)),
                    day=clone.due_date,
                    start_time=time.fromisoformat(str(plan["start_time"])),
                    duration_minutes=int(plan["duration_minutes"]),
                )
            )
    except Exception:  # noqa: BLE001 — see the docstring: the occurrence outranks its block
        logger.warning("recurrence auto-plan failed for task %s", clone.id, exc_info=True)


async def spawn_due_for_org(
    session: AsyncSession, org_id: uuid.UUID, *, ctx: Any | None = None
) -> int:
    """Spawn every schedule-mode occurrence whose ``next_run`` has arrived. Returns count."""
    # One lookup for the whole sweep: it bounds the query *and* prices every occurrence below,
    # and asking per task would be an N+1 the JSON could never show (docs/PERFORMANCE.md).
    today = await org_today(session, org_id)
    tasks = (
        await session.execute(
            select(Task).where(
                Task.org_id == org_id,
                Task.recurrence_next_run.is_not(None),
                Task.recurrence_next_run <= today,
            )
        )
    ).scalars().all()
    spawned = 0
    for task in tasks:
        rec = task.recurrence or {}
        if rec.get("mode") != RecurrenceMode.SCHEDULE.value:
            continue
        await spawn_next(session, org_id, task, actor_user_id=None, today=today, ctx=ctx)
        spawned += 1
    return spawned


async def spawn_scheduled_recurrences(ctx: dict) -> int:
    """ARQ cron entry point: materialize scheduled recurrences for every org."""
    from app.core.jobs import run_per_org, system_context

    total = 0

    async def _per_org(org, session) -> None:
        nonlocal total
        # A cron has nobody to authorize, so it drives the schedule service through the same
        # system context every other background writer uses (`app/core/jobs.py`) — which is what
        # lets an auto-planned occurrence emit its Google mirror and its notification from the
        # nightly sweep exactly as it does from a completion (#335 phase 5).
        total += await spawn_due_for_org(session, org.id, ctx=system_context(org, session))

    await run_per_org(_per_org)
    return total
