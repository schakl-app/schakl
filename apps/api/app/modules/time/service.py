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

from sqlalchemy import func, select
from sqlalchemy import text as sql_text

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

    def _ensure_not_locked(self, entry: TimeEntry) -> None:
        """Approved hours are signed off; only managers may still change them."""
        if entry.approved_at is not None and not self.ctx.role.can_manage:
            raise AppError("approved_locked", "errors.approved_locked", status_code=403)

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
        self._ensure_not_locked(entry)
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
        task_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> tuple[int, int]:
        """Team-wide logged minutes for a company/project/task (budget burn-down)."""
        stmt = self.repo.scoped_select().where(TimeEntry.ended_at.is_not(None))
        if company_id is not None:
            stmt = stmt.where(TimeEntry.company_id == company_id)
        if project_id is not None:
            stmt = stmt.where(TimeEntry.project_id == project_id)
        if task_id is not None:
            stmt = stmt.where(TimeEntry.task_id == task_id)
        if date_from is not None:
            stmt = stmt.where(
                TimeEntry.started_at >= datetime.combine(date_from, time.min, tzinfo=UTC)
            )
        if date_to is not None:
            stmt = stmt.where(
                TimeEntry.started_at
                < datetime.combine(date_to, time.min, tzinfo=UTC) + timedelta(days=1)
            )
        entries = (await self.ctx.session.execute(stmt)).scalars().all()
        minutes = sum(e.minutes for e in entries)
        billable = sum(e.minutes for e in entries if e.billable)
        return minutes, billable

    async def delete(self, entry_id: uuid.UUID) -> None:
        self.ctx.ensure_can_write()
        entry = await self._owned_or_404(entry_id)
        self._ensure_not_locked(entry)
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

    # --- approval / invoicing (manager surface) -------------------------------- #
    def _report_conditions(
        self,
        *,
        user_id: uuid.UUID | None,
        company_id: uuid.UUID | None,
        project_id: uuid.UUID | None,
        date_from: date | None,
        date_to: date | None,
        billable: bool | None,
        approved: bool | None,
        invoiced: bool | None,
    ) -> list:
        conditions = [TimeEntry.ended_at.is_not(None)]  # running timers aren't reviewable
        if user_id is not None:
            conditions.append(TimeEntry.user_id == user_id)
        if company_id is not None:
            conditions.append(TimeEntry.company_id == company_id)
        if project_id is not None:
            conditions.append(TimeEntry.project_id == project_id)
        if date_from is not None:
            conditions.append(
                TimeEntry.started_at >= datetime.combine(date_from, time.min, tzinfo=UTC)
            )
        if date_to is not None:
            conditions.append(
                TimeEntry.started_at
                < datetime.combine(date_to, time.min, tzinfo=UTC) + timedelta(days=1)
            )
        if billable is not None:
            conditions.append(TimeEntry.billable.is_(billable))
        if approved is not None:
            conditions.append(
                TimeEntry.approved_at.is_not(None) if approved else TimeEntry.approved_at.is_(None)
            )
        if invoiced is not None:
            conditions.append(
                TimeEntry.invoiced_at.is_not(None) if invoiced else TimeEntry.invoiced_at.is_(None)
            )
        return conditions

    async def report(
        self,
        *,
        limit: int,
        offset: int,
        user_id: uuid.UUID | None = None,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        billable: bool | None = None,
        approved: bool | None = None,
        invoiced: bool | None = None,
    ) -> tuple[Sequence[TimeEntry], int, dict[str, int]]:
        """Org-wide entries for the approval/invoicing overview. Managers only."""
        self.ctx.ensure_can_manage()
        conditions = self._report_conditions(
            user_id=user_id,
            company_id=company_id,
            project_id=project_id,
            date_from=date_from,
            date_to=date_to,
            billable=billable,
            approved=approved,
            invoiced=invoiced,
        )
        stmt = (
            self.repo.scoped_select()
            .where(*conditions)
            .order_by(TimeEntry.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        items = (await self.ctx.session.execute(stmt)).scalars().all()

        base = (
            select(
                func.count(),
                func.coalesce(func.sum(TimeEntry.minutes), 0),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(TimeEntry.billable.is_(True)), 0
                ),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(TimeEntry.approved_at.is_not(None)), 0
                ),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(TimeEntry.approved_at.is_(None)), 0
                ),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(
                        TimeEntry.approved_at.is_not(None),
                        TimeEntry.billable.is_(True),
                        TimeEntry.invoiced_at.is_(None),
                    ),
                    0,
                ),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(TimeEntry.invoiced_at.is_not(None)), 0
                ),
            )
            .select_from(TimeEntry)
            .where(TimeEntry.org_id == self.ctx.org.id, *conditions)
        )
        row = (await self.ctx.session.execute(base)).one()
        totals = {
            "count": int(row[0]),
            "minutes": int(row[1]),
            "billable_minutes": int(row[2]),
            "approved_minutes": int(row[3]),
            "open_minutes": int(row[4]),
            "to_invoice_minutes": int(row[5]),
            "invoiced_minutes": int(row[6]),
        }
        return items, totals["count"], totals

    # --- stats (manager surface) ------------------------------------------------ #
    async def productivity(
        self, *, date_from: date, date_to: date
    ) -> list[dict[str, object]]:
        """Per-employee totals for the productivity report. Managers only."""
        self.ctx.ensure_can_manage()
        start = datetime.combine(date_from, time.min, tzinfo=UTC)
        end = datetime.combine(date_to, time.min, tzinfo=UTC) + timedelta(days=1)
        stmt = (
            select(
                TimeEntry.user_id,
                func.coalesce(func.sum(TimeEntry.minutes), 0),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(TimeEntry.billable.is_(True)), 0
                ),
                func.coalesce(
                    func.sum(TimeEntry.minutes).filter(TimeEntry.approved_at.is_not(None)), 0
                ),
                func.count(),
                func.count(func.distinct(func.date(TimeEntry.started_at))),
            )
            .where(
                TimeEntry.org_id == self.ctx.org.id,
                TimeEntry.ended_at.is_not(None),
                TimeEntry.started_at >= start,
                TimeEntry.started_at < end,
            )
            .group_by(TimeEntry.user_id)
        )
        rows = (await self.ctx.session.execute(stmt)).all()
        result = [
            {
                "user_id": r[0],
                "minutes": int(r[1]),
                "billable_minutes": int(r[2]),
                "approved_minutes": int(r[3]),
                "entry_count": int(r[4]),
                "active_days": int(r[5]),
            }
            for r in rows
        ]
        result.sort(key=lambda r: r["minutes"], reverse=True)  # type: ignore[arg-type, return-value]
        return result

    async def revenue(self, *, year: int) -> dict[str, object]:
        """Omzet per month (selected + previous year) and per client (selected year).

        Joins ``projects`` by table name only (mirroring the FK convention — no import of
        the projects module); both sides carry the explicit org filter and RLS backs it up.
        """
        self.ctx.ensure_can_manage()
        params = {
            "org_id": str(self.ctx.org.id),
            "start": datetime(year - 1, 1, 1, tzinfo=UTC),
            "end": datetime(year + 1, 1, 1, tzinfo=UTC),
        }
        month_rows = (
            await self.ctx.session.execute(
                sql_text(
                    """
                    SELECT CAST(date_part('year', te.started_at) AS int) AS y,
                           CAST(date_part('month', te.started_at) AS int) AS m,
                           COALESCE(SUM(te.minutes / 60.0 * p.hourly_rate), 0) AS revenue
                    FROM time_entries te
                    JOIN projects p ON p.id = te.project_id AND p.org_id = te.org_id
                    WHERE te.org_id = :org_id
                      AND te.billable AND te.ended_at IS NOT NULL
                      AND p.hourly_rate IS NOT NULL
                      AND te.started_at >= :start AND te.started_at < :end
                    GROUP BY 1, 2
                    """
                ),
                params,
            )
        ).all()
        months_current = [0.0] * 12
        months_previous = [0.0] * 12
        for row_year, row_month, row_revenue in month_rows:
            bucket = months_current if row_year == year else months_previous
            bucket[row_month - 1] = round(float(row_revenue), 2)

        client_rows = (
            await self.ctx.session.execute(
                sql_text(
                    """
                    SELECT te.company_id,
                           COALESCE(SUM(te.minutes / 60.0 * p.hourly_rate), 0) AS revenue
                    FROM time_entries te
                    JOIN projects p ON p.id = te.project_id AND p.org_id = te.org_id
                    WHERE te.org_id = :org_id
                      AND te.billable AND te.ended_at IS NOT NULL
                      AND p.hourly_rate IS NOT NULL
                      AND te.started_at >= :year_start AND te.started_at < :year_end
                    GROUP BY te.company_id
                    ORDER BY revenue DESC
                    """
                ),
                {
                    "org_id": str(self.ctx.org.id),
                    "year_start": datetime(year, 1, 1, tzinfo=UTC),
                    "year_end": datetime(year + 1, 1, 1, tzinfo=UTC),
                },
            )
        ).all()
        top = [
            {"company_id": r[0], "revenue": round(float(r[1]), 2)} for r in client_rows[:10]
        ]
        other = round(sum(float(r[1]) for r in client_rows[10:]), 2)

        return {
            "year": year,
            "months_current": months_current,
            "months_previous": months_previous,
            "total_current": round(sum(months_current), 2),
            "total_previous": round(sum(months_previous), 2),
            "top_clients": top,
            "other_revenue": other,
        }

    async def _entries_by_ids(self, entry_ids: list[uuid.UUID]) -> Sequence[TimeEntry]:
        stmt = self.repo.scoped_select().where(
            TimeEntry.id.in_(entry_ids), TimeEntry.ended_at.is_not(None)
        )
        return (await self.ctx.session.execute(stmt)).scalars().all()

    async def set_approval(self, entry_ids: list[uuid.UUID], approved: bool) -> int:
        """(Un)approve entries — the sign-off a manager gives before invoicing."""
        self.ctx.ensure_can_manage()
        entries = await self._entries_by_ids(entry_ids)
        for entry in entries:
            entry.approved_at = _now() if approved else None
            entry.approved_by_user_id = self.ctx.user.id if approved else None
            if not approved:
                entry.invoiced_at = None  # unapproving also clears the invoiced mark
        await self.ctx.session.flush()
        return len(entries)

    async def set_invoiced(self, entry_ids: list[uuid.UUID], invoiced: bool) -> int:
        self.ctx.ensure_can_manage()
        entries = await self._entries_by_ids(entry_ids)
        for entry in entries:
            entry.invoiced_at = _now() if invoiced else None
            if invoiced and entry.approved_at is None:
                # Invoicing implies sign-off: never invoiced-but-not-approved.
                entry.approved_at = _now()
                entry.approved_by_user_id = self.ctx.user.id
        await self.ctx.session.flush()
        return len(entries)

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
        # rows keyed by (company_id, project_id, task_id)
        rows: dict[
            tuple[uuid.UUID | None, uuid.UUID | None, uuid.UUID | None], list[int]
        ] = {}
        for e in entries:
            idx = (e.started_at.astimezone(UTC).date() - week_start).days
            if 0 <= idx < 7:
                key = (e.company_id, e.project_id, e.task_id)
                rows.setdefault(key, [0] * 7)[idx] += e.minutes

        row_models = [
            TimesheetRow(
                company_id=k[0], project_id=k[1], task_id=k[2], minutes=v, total=sum(v)
            )
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
