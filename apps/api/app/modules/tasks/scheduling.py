"""Task scheduling (#188): planned time blocks for a task on a calendar.

A *schedule* is when someone intends to work on a task — distinct from its ``due_date`` (a
deadline) and ``allocated_minutes`` (a budget). A task may carry several blocks. This module is
its own service + sub-router so the big ``TaskService`` stays about the card itself.

Authorization is deny-by-default and scoped (CLAUDE.md §15): a member holds ``tasks.schedule.*
:own`` and may plan **their own** time; ``:any`` is the manager grant that schedules anyone and
overlays a colleague's feed. Blocks are personal planning, so a member never reads another
person's block — a scope-aware fetch raises 404 rather than leaking that it exists.

The service is the authority on the block: instants are ``TIMESTAMPTZ``/UTC, the web and the
Google push render them in the org timezone, and ``hours`` is never accepted from a client. On
save it emits ``task.scheduled`` (notify the scheduled person) and ``task_schedule.saved``
(Google Calendar mirror); on removal ``task_schedule.removed`` deletes the pushed event —
including when the *task* is deleted and the blocks leave by FK cascade, which announces
nothing on its own (``remove_for_task``).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy import text as sql_text

from app.core.auth.models import User
from app.core.busy import BusyItem, busy_items, registered_busy_sources
from app.core.events import emit
from app.core.members import active_member_clause
from app.core.permissions.deps import require_permission
from app.core.scope import entity_visible
from app.core.tenancy import RequestContext, require_context
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.tasks.models import Task, TaskSchedule
from app.modules.tasks.schemas import (
    BusyFeedRead,
    BusyItemRead,
    ScheduleBatchCreate,
    ScheduleCreate,
    ScheduleItem,
    ScheduleLogTime,
    ScheduleRead,
    ScheduleUpdate,
)


def _display_name(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


def _window(day: date, start: time, minutes: int, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Combine a local day + start time + length into UTC instants. Interpreting the wall time in
    ``zone`` (never UTC) is what makes a day-drag DST-correct and keeps the balance out of the
    calculation (§8)."""
    starts_at = datetime.combine(day, start).replace(tzinfo=zone)
    return starts_at, starts_at + timedelta(minutes=minutes)


class TaskScheduleService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TaskSchedule)

    # --- access scoping (issue #19) ------------------------------------------ #
    async def _readable_or_404(self, schedule_id: uuid.UUID) -> TaskSchedule:
        """A block the caller may see: their own, or anyone's with ``:any``. 404 (not 403) for
        someone else's block held by a member — planning is personal, so its existence is not
        leaked."""
        block = await self.repo.get(schedule_id)
        if block is None or not self._can_read(block.user_id):
            raise AppError("not_found", "errors.not_found", status_code=404)
        return block

    def _can_read(self, user_id: uuid.UUID | None) -> bool:
        if self.ctx.can("tasks.schedule.read", scope="any"):
            return True
        return user_id == self.ctx.user.id and self.ctx.can("tasks.schedule.read", scope="own")

    def _ensure_write_for(self, user_id: uuid.UUID | None) -> None:
        """Scheduling a block for ``user_id``: ``:any`` schedules anyone, ``:own`` only yourself."""
        if self.ctx.can("tasks.schedule.write", scope="any"):
            return
        if user_id == self.ctx.user.id and self.ctx.can("tasks.schedule.write", scope="own"):
            return
        raise AppError("forbidden", "errors.forbidden", status_code=403)

    # ------------------------------------------------------------------ #
    # Read
    # ------------------------------------------------------------------ #
    async def list_in_range(
        self,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        user_ids: list[uuid.UUID] | None = None,
        task_id: uuid.UUID | None = None,
    ) -> list[ScheduleItem]:
        """Blocks overlapping ``[date_from, date_to]`` (org-local days), decorated with the task
        and the person for a one-fetch calendar/timesheet feed.

        No ``user_ids`` → the caller's own blocks (the personal feed). Explicit ``user_ids`` →
        those people's blocks, which needs ``:any`` unless every id is the caller's own (the
        per-person team overlay). ``task_id`` narrows to one task (the task page's panel, which
        wants *every* block regardless of date) — so the window is optional when it is set, but
        one of a range or a task is always required to keep the query bounded.
        """
        if task_id is None and (date_from is None or date_to is None):
            raise AppError("required", "errors.required", status_code=422)
        can_any = self.ctx.can("tasks.schedule.read", scope="any")
        # An external login (a client's contact person) holds ``:own`` and owns no block —
        # every block is a colleague's — so for them *own* means "on a task I may read": the
        # planned work on their own account, which is the one thing a client wants from a
        # planning panel. The task's own portal horizon is the gate (visible to the client,
        # one of their companies), asked through the record's repository; a task they may not
        # open answers as if it had no blocks, exactly as the task itself 404s. Without a task
        # the personal feed stays what it is for everyone: the caller's own blocks, i.e. none.
        portal = self.ctx.is_portal and not can_any
        if portal and task_id is not None:
            if user_ids or not await entity_visible(self.ctx, "task", task_id):
                return []
        targets: list[uuid.UUID] | None
        if user_ids:
            if not can_any and set(user_ids) - {self.ctx.user.id}:
                raise AppError("forbidden", "errors.forbidden", status_code=403)
            targets = user_ids
        elif task_id is not None:
            targets = None if (can_any or portal) else [self.ctx.user.id]
        else:
            targets = [self.ctx.user.id]

        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        stmt = (
            select(
                TaskSchedule,
                Task.title,
                Task.project_id,
                Task.company_id,
                Task.status,
                Task.allocated_minutes,
                User.full_name,
                User.email,
            )
            .join(Task, Task.id == TaskSchedule.task_id)
            .outerjoin(User, User.id == TaskSchedule.user_id)
            .where(TaskSchedule.org_id == self.ctx.org.id)
            .order_by(TaskSchedule.starts_at)
        )
        if date_from is not None and date_to is not None:
            window_start = datetime.fromisoformat(date_from.isoformat()).replace(tzinfo=zone)
            window_end = (
                datetime.fromisoformat(date_to.isoformat()).replace(tzinfo=zone)
                + timedelta(days=1)
            )
            stmt = stmt.where(
                TaskSchedule.starts_at < window_end, TaskSchedule.ends_at > window_start
            )
        if task_id is not None:
            stmt = stmt.where(TaskSchedule.task_id == task_id)
        else:
            # The calendar feed stops drawing a departed colleague's blocks the moment the
            # roster menus stop offering the person (#439) — the rows themselves stay, and the
            # task page's own panel (``task_id`` set) keeps showing them, because a record
            # surface keeps its record. A block with no person is the system's and stays too.
            stmt = stmt.where(
                or_(
                    TaskSchedule.user_id.is_(None),
                    active_member_clause(self.ctx.org.id, TaskSchedule.user_id),
                )
            )
        if targets is not None:
            stmt = stmt.where(TaskSchedule.user_id.in_(targets))

        rows = (await self.ctx.session.execute(stmt)).all()
        items: list[ScheduleItem] = []
        for block, title, project_id, company_id, status, allocated, full_name, email in rows:
            local_start = block.starts_at.astimezone(zone)
            # A block ending exactly at local midnight belongs to the day it ran *into*, minus one.
            local_end = block.ends_at.astimezone(zone) - timedelta(microseconds=1)
            items.append(
                ScheduleItem(
                    id=block.id,
                    task_id=block.task_id,
                    user_id=block.user_id,
                    starts_at=block.starts_at,
                    ends_at=block.ends_at,
                    start=local_start.date(),
                    end=max(local_start.date(), local_end.date()),
                    # A client reads the window and who is in it, never the planner's
                    # note, the hour budget or the time entry the block became — those are
                    # the agency's working notes on its own diary (the tasks list nulls
                    # ``allocated_minutes`` for a portal caller for the same reason).
                    note=None if portal else block.note,
                    time_entry_id=None if portal else block.time_entry_id,
                    created_by_user_id=block.created_by_user_id,
                    created_by_name=block.created_by_name,
                    user_name=full_name or email,
                    task_title=title,
                    project_id=project_id,
                    company_id=company_id,
                    status=status,
                    allocated_minutes=None if portal else allocated,
                )
            )
        return items

    async def get(self, schedule_id: uuid.UUID) -> TaskSchedule:
        return await self._readable_or_404(schedule_id)

    async def busy(
        self, *, user_ids: list[uuid.UUID], date_from: date, date_to: date
    ) -> BusyFeedRead:
        """What is already on these people's calendars — the conflict check behind Inplannen.

        Gated on the **write**: being allowed to put a block on somebody's calendar is the
        reason to see what is already on it, which is also Google's free/busy rule. A member
        holding ``:own`` may ask about themselves and nobody else; ``:any`` asks about anyone.
        Whether the answer carries *titles* is each provider's own read rule (``core/busy.py``)
        — this route decides that the person is taken, never what by.
        """
        if date_to < date_from or (date_to - date_from).days > 31:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"date_to": "errors.validation"},
                details={"max_days": 31},
            )
        targets = list(dict.fromkeys(user_ids))
        for user_id in targets:
            self._ensure_write_for(user_id)
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        window_start = datetime.combine(date_from, time.min).replace(tzinfo=zone)
        window_end = datetime.combine(date_to, time.min).replace(tzinfo=zone) + timedelta(days=1)
        items, failed = await busy_items(self.ctx, targets, window_start, window_end)
        return BusyFeedRead(
            items=[BusyItemRead(**item.__dict__) for item in items],
            sources=registered_busy_sources(),
            unavailable=failed,
        )

    # ------------------------------------------------------------------ #
    # Write
    # ------------------------------------------------------------------ #
    async def create(self, data: ScheduleCreate) -> TaskSchedule:
        task = await self.ctx.repo(Task).get_or_404(data.task_id)
        # Default the block to the task's assignee (UX.md: show the inherited value), then the
        # scheduler themselves so a task with no assignee is still plannable.
        user_id = data.user_id or task.assignee_user_id or self.ctx.user.id
        self._ensure_write_for(user_id)
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        starts_at, ends_at = _window(data.day, data.start_time, data.duration_minutes, zone)
        return await self._book(task, user_id, starts_at, ends_at, data.note)

    async def create_many(self, data: ScheduleBatchCreate) -> list[TaskSchedule]:
        """The same block on several people's calendars — one row each, one call.

        All-or-nothing: every person is checked against the caller's scope *before* the first
        row is written, so a member naming a colleague beside themselves gets the 403 and books
        nobody, rather than booking themselves and then being refused. The order is the
        caller's (the chips, left to right); a person named twice is booked once.
        """
        task = await self.ctx.repo(Task).get_or_404(data.task_id)
        user_ids = list(dict.fromkeys(data.user_ids))
        for user_id in user_ids:
            self._ensure_write_for(user_id)
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        starts_at, ends_at = _window(data.day, data.start_time, data.duration_minutes, zone)
        return [
            await self._book(task, user_id, starts_at, ends_at, data.note) for user_id in user_ids
        ]

    async def _book(
        self,
        task: Task,
        user_id: uuid.UUID,
        starts_at: datetime,
        ends_at: datetime,
        note: str | None,
    ) -> TaskSchedule:
        """Write one block and announce it — the one place a block is born, so the mirror and the
        notification cannot be forgotten by the next way of creating one."""
        # A background writer (the recurrence auto-plan's cron, #335) has no person behind it: its
        # ``system_context`` user is a placeholder that exists in no table, and
        # ``created_by_user_id``'s FK would refuse it — the same rule ``Task.ai_status`` already
        # states (#327). A NULL scheduler *is* the system, which is what the snapshot column pair
        # was built to distinguish (issue #64).
        by_system = getattr(self.ctx, "is_system", False)
        block = await self.repo.create(
            task_id=task.id,
            user_id=user_id,
            starts_at=starts_at,
            ends_at=ends_at,
            note=note,
            created_by_user_id=None if by_system else self.ctx.user.id,
            created_by_name=None if by_system else _display_name(self.ctx.user),
        )
        await self._emit_saved(block, task)
        await self._notify_scheduled(block, task)
        return block

    async def update(
        self, schedule_id: uuid.UUID, data: ScheduleUpdate, *, notify: bool = True
    ) -> TaskSchedule:
        """``notify=False`` moves a block without telling its new owner — for the series
        hand-off, where the person taking over a year of a task has already heard "toegewezen"
        once and must not hear "ingepland" twelve times over. The Google mirror still fires:
        an event on the wrong calendar is a fact, a duplicate notification is noise."""
        block = await self._readable_or_404(schedule_id)
        # Editing an existing block needs write on its *current* owner…
        self._ensure_write_for(block.user_id)
        old_user_id = block.user_id

        if data.user_id is not None and data.user_id != block.user_id:
            # …and reassigning it needs write on the *new* owner too.
            self._ensure_write_for(data.user_id)
            block.user_id = data.user_id

        # Any omitted field keeps the block's current local value, derived from the stored
        # instants in the org timezone — so a plain day-move preserves the wall-clock time.
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        local_start = block.starts_at.astimezone(zone)
        day = data.day if data.day is not None else local_start.date()
        start_time = data.start_time if data.start_time is not None else local_start.time()
        minutes = (
            data.duration_minutes
            if data.duration_minutes is not None
            else round((block.ends_at - block.starts_at).total_seconds() / 60)
        )
        block.starts_at, block.ends_at = _window(day, start_time, minutes, zone)
        if data.note is not None:
            block.note = data.note
        await self.ctx.session.flush()

        task = await self.ctx.repo(Task).get_or_404(block.task_id)
        await self._emit_saved(block, task)
        # A reassignment tells the new person; a plain move does not re-notify (avoid churn).
        if notify and block.user_id is not None and block.user_id != old_user_id:
            await self._notify_scheduled(block, task)
        return block

    async def delete(self, schedule_id: uuid.UUID) -> None:
        block = await self._readable_or_404(schedule_id)
        self._ensure_write_for(block.user_id)
        await self._emit_removed(block)
        await self.repo.delete(block)

    async def remove_for_task(self, task_id: uuid.UUID) -> None:
        """Announce every block of a task that is about to be deleted.

        ``task_schedules.task_id`` is ``ON DELETE CASCADE``, so the blocks already go with the
        card — in the database. What the cascade cannot do is *say so*: the Google mirror only
        learns a block is gone from ``task_schedule.removed``, and a link left at ``pushed``
        points at a row that no longer exists, so nothing will ever clean the event up. The rows
        still go with the cascade; this adds the sentence.

        Deliberately unscoped: the caller is deleting the task, which they were already allowed
        to do, and the blocks go whether or not they hold ``tasks.schedule.write`` on the people
        they belong to (§16 — recording a consequence is not its own grant). Refusing here would
        make a colleague's block a reason a manager cannot delete a task.
        """
        blocks = (
            (
                await self.ctx.session.execute(
                    self.repo.scoped_select().where(TaskSchedule.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )
        for block in blocks:
            await self._emit_removed(block)

    async def refresh_for_task(self, task: Task) -> None:
        """Re-announce every block of a task whose words changed (a rename, a rewritten
        description).

        The Google mirror pushes a *snapshot* and deliberately never re-reads a task
        (``google/calendar/push.py``), so an edit that changes what the event says has to
        re-emit — otherwise every mirrored block keeps the old title forever. Same emit site
        as create/update (#188), so the mirror is never reached into from the task service.

        Deliberately unscoped, like ``remove_for_task``: the caller was allowed to edit the
        task, and the blocks follow it whoever they belong to — recording a consequence is not
        its own grant (§16).
        """
        blocks = (
            (
                await self.ctx.session.execute(
                    self.repo.scoped_select().where(TaskSchedule.task_id == task.id)
                )
            )
            .scalars()
            .all()
        )
        company_name = await self._company_name(task.company_id)
        for block in blocks:
            await self._emit_saved(block, task, company_name=company_name)

    async def log_time(self, schedule_id: uuid.UUID, data: ScheduleLogTime) -> TaskSchedule:
        """Confirm a passed block as a real time entry (#188). The entry is always the *caller's*
        own worked time (``TimeService.create`` fixes ``user_id`` to them), so only the person the
        block is for may log it. Linking the entry back marks the block logged and stops it being
        counted twice."""
        from app.modules.time.schemas import TimeEntryCreate
        from app.modules.time.service import TimeService

        block = await self._readable_or_404(schedule_id)
        if block.user_id != self.ctx.user.id:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        if block.time_entry_id is not None:
            raise AppError(
                "schedule_already_logged", "errors.schedule_already_logged", status_code=409
            )
        task = await self.ctx.repo(Task).get_or_404(block.task_id)
        # Either the block's own duration (ended_at) or the user's corrected minutes.
        entry = await TimeService(self.ctx).create(
            TimeEntryCreate(
                task_id=task.id,
                project_id=task.project_id,
                company_id=task.company_id,
                started_at=block.starts_at,
                ended_at=block.ends_at if data.minutes is None else None,
                minutes=data.minutes,
                break_minutes=data.break_minutes,
                description=data.description,
                billable=data.billable,
                entry_type_key=data.entry_type_key,
            )
        )
        block.time_entry_id = entry.id
        await self.ctx.session.flush()
        return block

    async def mark_logged(
        self, schedule_id: uuid.UUID, *, task_id: uuid.UUID, entry_id: uuid.UUID
    ) -> None:
        """Claim a block for an entry someone else built (#314), under ``log_time``'s own rules.

        The finish prompt writes its entry through the time module's published surface with the
        hours the user confirmed — which may be neither the block's start nor its length — so it
        cannot go through ``log_time``, whose entry *is* the block's window. What it must still
        inherit is the guarantee: a block linked to an entry stops offering "Uren registreren",
        which is the only thing stopping the same afternoon being booked twice. Hence the same
        three refusals, in the same order.
        """
        block = await self._readable_or_404(schedule_id)
        if block.task_id != task_id:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"schedule_id": "errors.validation"},
            )
        if block.user_id != self.ctx.user.id:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        if block.time_entry_id is not None:
            raise AppError(
                "schedule_already_logged", "errors.schedule_already_logged", status_code=409
            )
        block.time_entry_id = entry_id
        await self.ctx.session.flush()

    # ------------------------------------------------------------------ #
    # Bus emits (CLAUDE.md §6 — never import the google/notifications internals)
    # ------------------------------------------------------------------ #
    async def _company_name(self, company_id: uuid.UUID | None) -> str | None:
        """The client's *label* (``companies.name``, never ``legal_name`` — a calendar is a
        list, not a document; ``app/core/naming.py``), read with org-scoped SQL rather than an
        import of the companies module (§6)."""
        if company_id is None:
            return None
        return await self.ctx.session.scalar(
            sql_text("SELECT name FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self.ctx.org.id},
        )

    async def _emit_saved(
        self, block: TaskSchedule, task: Task, *, company_name: str | None = None
    ) -> None:
        """Mirror the block to the person's Google Calendar (#188), worded in the org timezone.
        The snapshot is everything the google handler needs — it never re-reads a task. The
        client's name rides along because the event is titled by it: a week of "Taak: …" says
        what kind of thing each block is and never whose work it is."""
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        local_start = block.starts_at.astimezone(zone)
        local_end = block.ends_at.astimezone(zone)
        if company_name is None:
            company_name = await self._company_name(task.company_id)
        await emit(
            "task_schedule.saved",
            self.ctx,
            {
                "schedule_id": block.id,
                "user_id": block.user_id,
                "task_id": task.id,
                "task_title": task.title,
                "company_name": company_name,
                "task_description": task.description,
                "start_date": local_start.date().isoformat(),
                "end_date": local_end.date().isoformat(),
                "start_time": local_start.strftime("%H:%M:%S"),
                "end_time": local_end.strftime("%H:%M:%S"),
                "timezone": str(zone),
            },
        )

    async def _emit_removed(self, block: TaskSchedule) -> None:
        """The block is going: delete whatever was pushed for it (#188). One emit site, so a
        second way of removing a block cannot forget the mirror the way the task cascade did."""
        await emit(
            "task_schedule.removed",
            self.ctx,
            {"schedule_id": block.id, "user_id": block.user_id},
        )

    async def _notify_scheduled(self, block: TaskSchedule, task: Task) -> None:
        """Tell the scheduled person their task was planned (#188). The actor is auto-excluded, so
        planning your own task is silent; a manager scheduling you notifies you, with the
        ``/tasks/{id}`` deeplink the notifications module builds for entity_type=task."""
        if block.user_id is None:
            return
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        await emit(
            "task.scheduled",
            self.ctx,
            {
                "task_id": task.id,
                "title": task.title,
                "scheduled_date": block.starts_at.astimezone(zone).date().isoformat(),
                "_recipients": [block.user_id],
            },
        )


# --------------------------------------------------------------------------- #
# The busy seam's tasks third (app/core/busy.py): planned blocks
# --------------------------------------------------------------------------- #
async def task_blocks_busy(
    ctx: RequestContext,
    user_ids: list[uuid.UUID],
    window_start: datetime,
    window_end: datetime,
) -> list[BusyItem]:
    """These people's planned blocks inside the window, titled where the caller may read them.

    The read rule is the one ``list_in_range`` already states — ``tasks.schedule.read:any``
    sees anyone's, ``:own`` only the caller's — so a manager who may book a colleague but not
    read their planning sees "bezet 09:00–11:00" rather than which client the colleague is
    working for. The window is never withheld: an unnamed block is honest, an invisible one is
    a double booking.
    """
    rows = (
        await ctx.session.execute(
            select(TaskSchedule, Task.title)
            .join(Task, Task.id == TaskSchedule.task_id)
            .where(
                TaskSchedule.org_id == ctx.org.id,
                TaskSchedule.user_id.in_(user_ids),
                TaskSchedule.starts_at < window_end,
                TaskSchedule.ends_at > window_start,
            )
            .order_by(TaskSchedule.starts_at)
        )
    ).all()
    read_any = ctx.can("tasks.schedule.read", scope="any")
    read_own = ctx.can("tasks.schedule.read", scope="own")
    items: list[BusyItem] = []
    for block, title in rows:
        readable = read_any or (read_own and block.user_id == ctx.user.id)
        items.append(
            BusyItem(
                user_id=block.user_id,
                starts_at=block.starts_at,
                ends_at=block.ends_at,
                source="tasks.schedule",
                title=title if readable else None,
                ref=str(block.id) if readable else None,
                href=f"/tasks/{block.task_id}" if readable else None,
            )
        )
    return items


# --------------------------------------------------------------------------- #
# Router — mounted under /api/v1/tasks/schedules (before /tasks/{task_id})
# --------------------------------------------------------------------------- #
scheduling_router = APIRouter(prefix="/schedules", tags=["tasks"])


@scheduling_router.get(
    "/busy",
    response_model=BusyFeedRead,
    dependencies=[require_permission("tasks.schedule.write")],
)
async def busy_schedules(
    user_ids: list[uuid.UUID] = Query(..., min_length=1, max_length=50),
    date_from: date = Query(...),
    date_to: date = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> BusyFeedRead:
    """When these people are already taken — every calendar the instance can read, in one
    answer, for the block about to be planned. Declared before ``/{schedule_id}`` so the path
    segment is never read as an id."""
    return await TaskScheduleService(ctx).busy(
        user_ids=user_ids, date_from=date_from, date_to=date_to
    )


@scheduling_router.get(
    "",
    response_model=list[ScheduleItem],
    dependencies=[require_permission("tasks.schedule.read")],
)
async def list_schedules(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    user_ids: list[uuid.UUID] | None = Query(None),
    task_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[ScheduleItem]:
    return await TaskScheduleService(ctx).list_in_range(
        date_from=date_from, date_to=date_to, user_ids=user_ids, task_id=task_id
    )


@scheduling_router.post(
    "",
    response_model=ScheduleRead,
    status_code=201,
    dependencies=[require_permission("tasks.schedule.write")],
)
async def create_schedule(
    payload: ScheduleCreate,
    ctx: RequestContext = Depends(require_context),
) -> ScheduleRead:
    block = await TaskScheduleService(ctx).create(payload)
    return ScheduleRead.model_validate(block)


@scheduling_router.post(
    "/batch",
    response_model=list[ScheduleRead],
    status_code=201,
    dependencies=[require_permission("tasks.schedule.write")],
)
async def create_schedules(
    payload: ScheduleBatchCreate,
    ctx: RequestContext = Depends(require_context),
) -> list[ScheduleRead]:
    """Schedule one task for several people at once: one block per person, all or nothing."""
    blocks = await TaskScheduleService(ctx).create_many(payload)
    return [ScheduleRead.model_validate(block) for block in blocks]


@scheduling_router.get(
    "/{schedule_id}",
    response_model=ScheduleRead,
    dependencies=[require_permission("tasks.schedule.read")],
)
async def get_schedule(
    schedule_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ScheduleRead:
    block = await TaskScheduleService(ctx).get(schedule_id)
    return ScheduleRead.model_validate(block)


@scheduling_router.patch(
    "/{schedule_id}",
    response_model=ScheduleRead,
    dependencies=[require_permission("tasks.schedule.write")],
)
async def update_schedule(
    schedule_id: uuid.UUID,
    payload: ScheduleUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ScheduleRead:
    block = await TaskScheduleService(ctx).update(schedule_id, payload)
    return ScheduleRead.model_validate(block)


@scheduling_router.delete(
    "/{schedule_id}",
    status_code=204,
    dependencies=[require_permission("tasks.schedule.write")],
)
async def delete_schedule(
    schedule_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaskScheduleService(ctx).delete(schedule_id)


@scheduling_router.post(
    "/{schedule_id}/log-time",
    response_model=ScheduleRead,
    dependencies=[require_permission("time.entry.write")],
)
async def log_schedule_time(
    schedule_id: uuid.UUID,
    payload: ScheduleLogTime,
    ctx: RequestContext = Depends(require_context),
) -> ScheduleRead:
    block = await TaskScheduleService(ctx).log_time(schedule_id, payload)
    return ScheduleRead.model_validate(block)
