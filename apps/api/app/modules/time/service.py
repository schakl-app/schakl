"""Business logic for time tracking (CLAUDE.md §6, §10).

Members manage their **own** entries; managers (owner/admin) may act on another user's entries by
passing ``user_id``. All reads/writes go through the tenant-scoped repository (Golden Rule 1).
A running timer is the single entry with ``ended_at IS NULL`` for a user; starting a new timer
auto-stops the current one.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.time.models import TimeEntry
from app.modules.time.schemas import (
    TimeEntryCreate,
    TimeEntryUpdate,
    TimerStart,
    Timesheet,
    TimesheetRow,
    TimeSummary,
)


def _now() -> datetime:
    return datetime.now(UTC)


def _duration_minutes(started_at: datetime, ended_at: datetime) -> int:
    return max(0, round((ended_at - started_at).total_seconds() / 60))


class TimeService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TimeEntry)

    # --- access scoping ------------------------------------------------------ #
    def _effective_user_id(self, user_id: uuid.UUID | None) -> uuid.UUID:
        """Resolve whose entries to act on: self, or another user if a manager asks."""
        if user_id is None or user_id == self.ctx.user.id:
            return self.ctx.user.id
        if not self.ctx.role.can_manage:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        return user_id

    async def _owned_or_404(self, entry_id: uuid.UUID) -> TimeEntry:
        entry = await self.repo.get_or_404(entry_id)
        if entry.user_id != self.ctx.user.id and not self.ctx.role.can_manage:
            # Don't reveal another user's entry exists.
            raise AppError("not_found", "errors.not_found", status_code=404)
        return entry

    # --- timer --------------------------------------------------------------- #
    async def running(self, user_id: uuid.UUID | None = None) -> TimeEntry | None:
        uid = self._effective_user_id(user_id)
        stmt = (
            self.repo.scoped_select()
            .where(TimeEntry.user_id == uid)
            .where(TimeEntry.ended_at.is_(None))
            .order_by(TimeEntry.started_at.desc())
            .limit(1)
        )
        return (await self.ctx.session.execute(stmt)).scalars().first()

    async def start_timer(self, data: TimerStart) -> TimeEntry:
        self.ctx.ensure_can_write()
        # Starting a new timer stops the current one (common time-tracker behaviour).
        current = await self.running()
        if current is not None:
            await self._stop(current)
        return await self.repo.create(
            user_id=self.ctx.user.id,
            started_at=_now(),
            ended_at=None,
            minutes=0,
            **data.model_dump(),
        )

    async def stop_timer(self) -> TimeEntry:
        self.ctx.ensure_can_write()
        current = await self.running()
        if current is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return await self._stop(current)

    async def _stop(self, entry: TimeEntry) -> TimeEntry:
        ended = _now()
        return await self.repo.update(
            entry, ended_at=ended, minutes=_duration_minutes(entry.started_at, ended)
        )

    # --- manual entries ------------------------------------------------------ #
    async def create(self, data: TimeEntryCreate) -> TimeEntry:
        self.ctx.ensure_can_write()
        values = data.model_dump()
        started_at = values["started_at"]
        ended_at = values.get("ended_at")
        minutes = values.get("minutes")
        brk = values.get("break_minutes") or 0
        if ended_at is not None:
            # Support overnight spans: roll the end forward a day if it isn't after the start.
            if ended_at <= started_at:
                ended_at += timedelta(days=1)
            values["ended_at"] = ended_at
            values["minutes"] = max(0, _duration_minutes(started_at, ended_at) - brk)
        elif minutes is not None:
            values["ended_at"] = started_at + timedelta(minutes=minutes + brk)
            values["minutes"] = minutes
        else:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"ended_at": "errors.required"},
            )
        return await self.repo.create(user_id=self.ctx.user.id, **values)

    async def update(self, entry_id: uuid.UUID, data: TimeEntryUpdate) -> TimeEntry:
        self.ctx.ensure_can_write()
        entry = await self._owned_or_404(entry_id)
        values = data.model_dump(exclude_unset=True)
        entry = await self.repo.update(entry, **values)
        # Keep worked minutes / end consistent for stopped entries when time fields change.
        if not entry.is_running:
            time_changed = {"started_at", "ended_at", "break_minutes"} & values.keys()
            if time_changed and entry.ended_at is not None:
                worked = _duration_minutes(entry.started_at, entry.ended_at) - (
                    entry.break_minutes or 0
                )
                entry = await self.repo.update(entry, minutes=max(0, worked))
            elif "minutes" in values:
                entry = await self.repo.update(
                    entry,
                    ended_at=entry.started_at
                    + timedelta(minutes=entry.minutes + (entry.break_minutes or 0)),
                )
        return entry

    # --- day view + logged aggregation --------------------------------------- #
    async def day(
        self, *, day: date, user_id: uuid.UUID | None = None
    ) -> tuple[date, Sequence[TimeEntry], int, int]:
        """A single day's entries (for the calendar day view) + total/billable minutes."""
        uid = self._effective_user_id(user_id)
        start = datetime.combine(day, time.min, tzinfo=UTC)
        entries = await self._entries_between(uid, start, start + timedelta(days=1))
        total = sum(e.minutes for e in entries if not e.is_running)
        billable = sum(e.minutes for e in entries if not e.is_running and e.billable)
        return day, entries, total, billable

    async def logged(
        self,
        *,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> tuple[int, int]:
        """Team-wide logged minutes for a company/project (for budget burn-down)."""
        stmt = self.repo.scoped_select().where(TimeEntry.ended_at.is_not(None))
        if company_id is not None:
            stmt = stmt.where(TimeEntry.company_id == company_id)
        if project_id is not None:
            stmt = stmt.where(TimeEntry.project_id == project_id)
        entries = (await self.ctx.session.execute(stmt)).scalars().all()
        minutes = sum(e.minutes for e in entries)
        billable = sum(e.minutes for e in entries if e.billable)
        return minutes, billable

    async def delete(self, entry_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        entry = await self._owned_or_404(entry_id)
        await self.repo.delete(entry)

    async def get(self, entry_id: uuid.UUID) -> TimeEntry:
        return await self._owned_or_404(entry_id)

    # --- queries ------------------------------------------------------------- #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        user_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[TimeEntry], int]:
        uid = self._effective_user_id(user_id)
        filters: dict = {"user_id": uid}
        if company_id is not None:
            filters["company_id"] = company_id
        items = await self.repo.list(
            limit=limit, offset=offset, order_by=TimeEntry.started_at.desc(), **filters
        )
        total = await self.repo.count(**filters)
        return items, total

    async def _entries_between(
        self, uid: uuid.UUID, start: datetime, end: datetime
    ) -> Sequence[TimeEntry]:
        stmt = (
            self.repo.scoped_select()
            .where(TimeEntry.user_id == uid)
            .where(TimeEntry.started_at >= start)
            .where(TimeEntry.started_at < end)
            .order_by(TimeEntry.started_at.asc())
        )
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def summary(
        self, *, day: date | None = None, user_id: uuid.UUID | None = None
    ) -> TimeSummary:
        uid = self._effective_user_id(user_id)
        day = day or _now().date()
        start = datetime.combine(day, time.min, tzinfo=UTC)
        entries = await self._entries_between(uid, start, start + timedelta(days=1))
        minutes = sum(e.minutes for e in entries if not e.is_running)
        running = await self.running(user_id=uid)
        return TimeSummary(date=day, minutes=minutes, running=running)  # type: ignore[arg-type]

    async def timesheet(
        self, *, week_start: date, user_id: uuid.UUID | None = None
    ) -> Timesheet:
        uid = self._effective_user_id(user_id)
        start = datetime.combine(week_start, time.min, tzinfo=UTC)
        entries = await self._entries_between(uid, start, start + timedelta(days=7))

        days = [week_start + timedelta(days=i) for i in range(7)]
        # rows keyed by (company_id, task_id)
        rows: dict[tuple[uuid.UUID | None, uuid.UUID | None], list[int]] = {}
        for e in entries:
            idx = (e.started_at.astimezone(UTC).date() - week_start).days
            if 0 <= idx < 7:
                key = (e.company_id, e.task_id)
                rows.setdefault(key, [0] * 7)[idx] += e.minutes

        row_models = [
            TimesheetRow(company_id=k[0], task_id=k[1], minutes=v, total=sum(v))
            for k, v in rows.items()
        ]
        day_totals = [sum(v[i] for v in rows.values()) for i in range(7)]
        return Timesheet(
            week_start=week_start,
            days=days,
            rows=row_models,
            day_totals=day_totals,
            total=sum(day_totals),
        )
