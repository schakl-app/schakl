"""REST endpoints for time tracking under ``/api/v1/time`` (CLAUDE.md §6, §9).

Timer (start/stop/current), manual entries (CRUD), a daily summary for the dashboard, and the
weekly timesheet grid. Reads/writes default to the current user; managers may pass ``user_id``.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.tenancy import RequestContext, require_context
from app.modules.time.schemas import (
    DayView,
    LoggedSummary,
    TimeEntryCreate,
    TimeEntryRead,
    TimeEntryUpdate,
    TimerStart,
    Timesheet,
    TimeSummary,
)
from app.modules.time.service import TimeService
from app.schemas import Page

router = APIRouter(prefix="/time", tags=["time"])


# --- timer ----------------------------------------------------------------- #
@router.get("/timer", response_model=TimeEntryRead | None)
async def current_timer(
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead | None:
    entry = await TimeService(ctx).running()
    return TimeEntryRead.model_validate(entry) if entry else None


@router.post("/timer/start", response_model=TimeEntryRead, status_code=201)
async def start_timer(
    payload: TimerStart,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).start_timer(payload)
    return TimeEntryRead.model_validate(entry)


@router.post("/timer/stop", response_model=TimeEntryRead)
async def stop_timer(
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).stop_timer()
    return TimeEntryRead.model_validate(entry)


# --- summary + timesheet --------------------------------------------------- #
@router.get("/summary", response_model=TimeSummary)
async def summary(
    day: date | None = Query(None, alias="date"),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> TimeSummary:
    return await TimeService(ctx).summary(day=day, user_id=user_id)


@router.get("/timesheet", response_model=Timesheet)
async def timesheet(
    week_start: date = Query(...),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Timesheet:
    return await TimeService(ctx).timesheet(week_start=week_start, user_id=user_id)


@router.get("/day", response_model=DayView)
async def day_view(
    day: date = Query(..., alias="date"),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> DayView:
    """One day's entries (calendar day view) plus total + billable minutes."""
    the_day, entries, total, billable = await TimeService(ctx).day(day=day, user_id=user_id)
    return DayView(
        date=the_day,
        total_minutes=total,
        billable_minutes=billable,
        entries=[TimeEntryRead.model_validate(e) for e in entries],
    )


@router.get("/logged", response_model=LoggedSummary)
async def logged(
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> LoggedSummary:
    """Team-wide logged minutes for a company/project (budget burn-down)."""
    minutes, billable = await TimeService(ctx).logged(
        company_id=company_id, project_id=project_id
    )
    return LoggedSummary(minutes=minutes, billable_minutes=billable)


# --- manual entries -------------------------------------------------------- #
@router.get("/entries", response_model=Page[TimeEntryRead])
async def list_entries(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID | None = Query(None),
    company_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Page[TimeEntryRead]:
    items, total = await TimeService(ctx).list(
        limit=limit, offset=offset, user_id=user_id, company_id=company_id
    )
    return Page(
        items=[TimeEntryRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/entries", response_model=TimeEntryRead, status_code=201)
async def create_entry(
    payload: TimeEntryCreate,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).create(payload)
    return TimeEntryRead.model_validate(entry)


@router.get("/entries/{entry_id}", response_model=TimeEntryRead)
async def get_entry(
    entry_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).get(entry_id)
    return TimeEntryRead.model_validate(entry)


@router.patch("/entries/{entry_id}", response_model=TimeEntryRead)
async def update_entry(
    entry_id: uuid.UUID,
    payload: TimeEntryUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).update(entry_id, payload)
    return TimeEntryRead.model_validate(entry)


@router.delete("/entries/{entry_id}", status_code=204)
async def delete_entry(
    entry_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TimeService(ctx).delete(entry_id)
