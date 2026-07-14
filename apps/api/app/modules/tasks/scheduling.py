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
(Google Calendar mirror); on removal ``task_schedule.removed`` deletes the pushed event.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.auth.models import User
from app.core.events import emit
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.tasks.models import Task, TaskSchedule
from app.modules.tasks.schemas import (
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
        targets: list[uuid.UUID] | None
        if user_ids:
            if not can_any and set(user_ids) - {self.ctx.user.id}:
                raise AppError("forbidden", "errors.forbidden", status_code=403)
            targets = user_ids
        elif task_id is not None:
            targets = None if can_any else [self.ctx.user.id]
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
                    note=block.note,
                    time_entry_id=block.time_entry_id,
                    created_by_user_id=block.created_by_user_id,
                    created_by_name=block.created_by_name,
                    user_name=full_name or email,
                    task_title=title,
                    project_id=project_id,
                    company_id=company_id,
                    status=status,
                    allocated_minutes=allocated,
                )
            )
        return items

    async def get(self, schedule_id: uuid.UUID) -> TaskSchedule:
        return await self._readable_or_404(schedule_id)

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

        block = await self.repo.create(
            task_id=task.id,
            user_id=user_id,
            starts_at=starts_at,
            ends_at=ends_at,
            note=data.note,
            created_by_user_id=self.ctx.user.id,
            created_by_name=_display_name(self.ctx.user),
        )
        await self._emit_saved(block, task)
        await self._notify_scheduled(block, task)
        return block

    async def update(self, schedule_id: uuid.UUID, data: ScheduleUpdate) -> TaskSchedule:
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
        if block.user_id is not None and block.user_id != old_user_id:
            await self._notify_scheduled(block, task)
        return block

    async def delete(self, schedule_id: uuid.UUID) -> None:
        block = await self._readable_or_404(schedule_id)
        self._ensure_write_for(block.user_id)
        await emit(
            "task_schedule.removed",
            self.ctx,
            {"schedule_id": block.id, "user_id": block.user_id},
        )
        await self.repo.delete(block)

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

    # ------------------------------------------------------------------ #
    # Bus emits (CLAUDE.md §6 — never import the google/notifications internals)
    # ------------------------------------------------------------------ #
    async def _emit_saved(self, block: TaskSchedule, task: Task) -> None:
        """Mirror the block to the person's Google Calendar (#188), worded in the org timezone.
        The snapshot is everything the google handler needs — it never re-reads a task."""
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        local_start = block.starts_at.astimezone(zone)
        local_end = block.ends_at.astimezone(zone)
        await emit(
            "task_schedule.saved",
            self.ctx,
            {
                "schedule_id": block.id,
                "user_id": block.user_id,
                "task_id": task.id,
                "task_title": task.title,
                "task_description": task.description,
                "start_date": local_start.date().isoformat(),
                "end_date": local_end.date().isoformat(),
                "start_time": local_start.strftime("%H:%M:%S"),
                "end_time": local_end.strftime("%H:%M:%S"),
                "timezone": str(zone),
            },
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
# Router — mounted under /api/v1/tasks/schedules (before /tasks/{task_id})
# --------------------------------------------------------------------------- #
scheduling_router = APIRouter(prefix="/schedules", tags=["tasks"])


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
