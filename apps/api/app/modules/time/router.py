"""REST endpoints for time tracking under ``/api/v1/time`` (CLAUDE.md §6, §9).

Timer (start/stop/current), manual entries (CRUD), a daily summary for the dashboard, and the
weekly timesheet grid. Reads/writes default to the current user; managers may pass ``user_id``.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.time.schemas import (
    BulkResult,
    DayView,
    EntryApproval,
    EntryInvoiced,
    LoggedSummary,
    ProductivityRow,
    ProductivityStats,
    ProjectCost,
    RevenueStats,
    TimeEntryCreate,
    TimeEntryDraftPayload,
    TimeEntryDraftRead,
    TimeEntryRead,
    TimeEntryTypeCreate,
    TimeEntryTypeRead,
    TimeEntryTypeUpdate,
    TimeEntryUpdate,
    TimeReport,
    TimerStart,
    Timesheet,
    TimeSummary,
)
from app.modules.time.service import TimeEntryTypeService, TimeService
from app.schemas import Page

router = APIRouter(prefix="/time", tags=["time"])


# --- time-entry types (#176) -------------------------------------------------- #
# Declared before ``/entries/{entry_id}`` cannot collide (distinct path), but grouped up top
# like the interaction kinds for symmetry.
@router.get(
    "/entry-types",
    response_model=list[TimeEntryTypeRead],
    dependencies=[require_permission("time.entry_type.read")],
)
async def list_entry_types(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[TimeEntryTypeRead]:
    items = await TimeEntryTypeService(ctx).list(include_inactive=include_inactive)
    return [TimeEntryTypeRead.model_validate(t) for t in items]


@router.post(
    "/entry-types",
    response_model=TimeEntryTypeRead,
    status_code=201,
    dependencies=[require_permission("time.entry_type.manage")],
)
async def create_entry_type(
    payload: TimeEntryTypeCreate,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryTypeRead:
    return TimeEntryTypeRead.model_validate(await TimeEntryTypeService(ctx).create(payload))


@router.patch(
    "/entry-types/{type_id}",
    response_model=TimeEntryTypeRead,
    dependencies=[require_permission("time.entry_type.manage")],
)
async def update_entry_type(
    type_id: uuid.UUID,
    payload: TimeEntryTypeUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryTypeRead:
    return TimeEntryTypeRead.model_validate(
        await TimeEntryTypeService(ctx).update(type_id, payload)
    )


@router.delete(
    "/entry-types/{type_id}",
    status_code=204,
    dependencies=[require_permission("time.entry_type.manage")],
)
async def delete_entry_type(
    type_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TimeEntryTypeService(ctx).delete(type_id)


# --- approval / invoicing overview (managers) -------------------------------- #
@router.get(
    "/report",
    response_model=TimeReport,
    dependencies=[require_permission("time.report.read")],
)
async def time_report(
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID | None = Query(None),
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    billable: bool | None = Query(None),
    approved: bool | None = Query(None),
    invoiced: bool | None = Query(None),
    entry_type: str | None = Query(None, max_length=50, description="Filter by entry type key"),
    sort: str | None = Query(
        None, description="date | employee | company | project | task | minutes | …, '-' desc"
    ),
    ctx: RequestContext = Depends(require_context),
) -> TimeReport:
    """Org-wide entries with filter + sign-off totals, for the hours overview."""
    items, total, totals = await TimeService(ctx).report(
        limit=limit,
        offset=offset,
        user_id=user_id,
        company_id=company_id,
        project_id=project_id,
        date_from=date_from,
        date_to=date_to,
        billable=billable,
        approved=approved,
        invoiced=invoiced,
        entry_type=entry_type,
        sort=sort,
    )
    return TimeReport(
        items=[TimeEntryRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
        totals=totals,  # type: ignore[arg-type]
    )


@router.get(
    "/stats/productivity",
    response_model=ProductivityStats,
    dependencies=[require_permission("time.report.read")],
)
async def productivity_stats(
    date_from: date = Query(...),
    date_to: date = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> ProductivityStats:
    """Per-employee hours/billable/approved aggregates (managers)."""
    rows = await TimeService(ctx).productivity(date_from=date_from, date_to=date_to)
    return ProductivityStats(
        date_from=date_from,
        date_to=date_to,
        rows=[ProductivityRow.model_validate(r) for r in rows],
    )


@router.get(
    "/stats/revenue",
    response_model=RevenueStats,
    dependencies=[require_permission("time.report.read")],
)
async def revenue_stats(
    year: int = Query(..., ge=2000, le=2100),
    ctx: RequestContext = Depends(require_context),
) -> RevenueStats:
    """Monthly omzet for the selected + previous year and the top clients (managers)."""
    return RevenueStats.model_validate(await TimeService(ctx).revenue(year=year))


@router.get(
    "/cost",
    response_model=ProjectCost,
    dependencies=[require_permission("time.report.read")],
)
async def project_cost(
    project_id: uuid.UUID = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> ProjectCost:
    """A project's cost from employee rates (#111). The service also demands
    ``leave.rate.read:any`` — the figure is salary-derived."""
    return ProjectCost.model_validate(await TimeService(ctx).project_cost(project_id))


@router.post(
    "/entries/approve",
    response_model=BulkResult,
    dependencies=[require_permission("time.entry.approve")],
)
async def approve_entries(
    payload: EntryApproval,
    ctx: RequestContext = Depends(require_context),
) -> BulkResult:
    updated = await TimeService(ctx).set_approval(payload.entry_ids, payload.approved)
    return BulkResult(updated=updated)


@router.post(
    "/entries/invoice",
    response_model=BulkResult,
    dependencies=[require_permission("time.entry.invoice")],
)
async def invoice_entries(
    payload: EntryInvoiced,
    ctx: RequestContext = Depends(require_context),
) -> BulkResult:
    updated = await TimeService(ctx).set_invoiced(payload.entry_ids, payload.invoiced)
    return BulkResult(updated=updated)


# --- timer ----------------------------------------------------------------- #
@router.get(
    "/timer",
    response_model=TimeEntryRead | None,
    dependencies=[require_permission("time.entry.read")],
)
async def current_timer(
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead | None:
    entry = await TimeService(ctx).running()
    return TimeEntryRead.model_validate(entry) if entry else None


@router.post(
    "/timer/start",
    response_model=TimeEntryRead,
    status_code=201,
    dependencies=[require_permission("time.entry.write")],
)
async def start_timer(
    payload: TimerStart,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).start_timer(payload)
    return TimeEntryRead.model_validate(entry)


@router.post(
    "/timer/stop",
    response_model=TimeEntryRead,
    dependencies=[require_permission("time.entry.write")],
)
async def stop_timer(
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).stop_timer()
    return TimeEntryRead.model_validate(entry)


# --- summary + timesheet --------------------------------------------------- #
@router.get(
    "/summary",
    response_model=TimeSummary,
    dependencies=[require_permission("time.entry.read")],
)
async def summary(
    day: date | None = Query(None, alias="date"),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> TimeSummary:
    return await TimeService(ctx).summary(day=day, user_id=user_id)


@router.get(
    "/timesheet",
    response_model=Timesheet,
    dependencies=[require_permission("time.entry.read")],
)
async def timesheet(
    week_start: date = Query(...),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Timesheet:
    return await TimeService(ctx).timesheet(week_start=week_start, user_id=user_id)


@router.get(
    "/day",
    response_model=DayView,
    dependencies=[require_permission("time.entry.read")],
)
async def day_view(
    day: date = Query(..., alias="date"),
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> DayView:
    """One day's entries (calendar day view) plus total + billable minutes."""
    service = TimeService(ctx)
    the_day, entries, total, billable = await service.day(day=day, user_id=user_id)
    # The caller's own draft rides along (#44) — never attached to someone else's day.
    own = user_id is None or user_id == ctx.user.id
    draft = await service.draft_get(day) if own else None
    return DayView(
        date=the_day,
        total_minutes=total,
        billable_minutes=billable,
        entries=[TimeEntryRead.model_validate(e) for e in entries],
        draft=TimeEntryDraftRead.model_validate(draft) if draft else None,
    )


# --- drafts (#44): the autosaved in-progress registration ------------------ #
@router.put(
    "/drafts/{entry_date}",
    response_model=TimeEntryDraftRead,
    dependencies=[require_permission("time.entry.write")],
)
async def upsert_draft(
    entry_date: date,
    payload: TimeEntryDraftPayload,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryDraftRead:
    """Upsert the caller's own draft for a day. Author-only by construction."""
    draft = await TimeService(ctx).draft_upsert(entry_date, payload)
    return TimeEntryDraftRead.model_validate(draft)


@router.delete(
    "/drafts/{entry_date}",
    status_code=204,
    dependencies=[require_permission("time.entry.write")],
)
async def delete_draft(
    entry_date: date,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Discard the caller's own draft. Idempotent."""
    await TimeService(ctx).draft_delete(entry_date)


# Declared with **no scope** on purpose: a project's logged hours are team-visible, and this
# is what draws every budget bar. Narrowing it to ``:any`` removes them for every member (#19).
@router.get(
    "/logged",
    response_model=LoggedSummary,
    dependencies=[require_permission("time.entry.read")],
)
async def logged(
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    task_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> LoggedSummary:
    """Team-wide logged minutes for a company/project/task (budget burn-down)."""
    minutes, billable = await TimeService(ctx).logged(
        company_id=company_id,
        project_id=project_id,
        task_id=task_id,
        date_from=date_from,
        date_to=date_to,
    )
    return LoggedSummary(minutes=minutes, billable_minutes=billable)


# --- manual entries -------------------------------------------------------- #
# Also unscoped: ``all_users`` is free when the query names a company/project/task, and the
# service escalates to ``scope="any"`` only for the unscoped, org-wide report.
@router.get(
    "/entries",
    response_model=Page[TimeEntryRead],
    dependencies=[require_permission("time.entry.read")],
)
async def list_entries(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    user_id: uuid.UUID | None = Query(None),
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    task_id: uuid.UUID | None = Query(None),
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    entry_type: str | None = Query(None, max_length=50, description="Filter by entry type key"),
    running: bool | None = Query(None, description="Filter running timers in/out; unset = both"),
    all_users: bool = Query(
        False,
        description=(
            "The whole team's entries, not just mine. Free to anyone when the query names a "
            "company/project/task — those hours already show as a budget bar; managers only "
            "when it doesn't."
        ),
    ),
    sort: str | None = Query(
        None, description="date | employee | company | project | task | minutes | …, '-' desc"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[TimeEntryRead]:
    items, total = await TimeService(ctx).list(
        limit=limit,
        offset=offset,
        user_id=user_id,
        company_id=company_id,
        project_id=project_id,
        task_id=task_id,
        date_from=date_from,
        date_to=date_to,
        running=running,
        all_users=all_users,
        sort=sort,
        entry_type=entry_type,
    )
    return Page(
        items=[TimeEntryRead.model_validate(e) for e in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/entries",
    response_model=TimeEntryRead,
    status_code=201,
    dependencies=[require_permission("time.entry.write")],
)
async def create_entry(
    payload: TimeEntryCreate,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).create(payload)
    return TimeEntryRead.model_validate(entry)


@router.get(
    "/entries/{entry_id}",
    response_model=TimeEntryRead,
    dependencies=[require_permission("time.entry.read")],
)
async def get_entry(
    entry_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).get(entry_id)
    return TimeEntryRead.model_validate(entry)


@router.patch(
    "/entries/{entry_id}",
    response_model=TimeEntryRead,
    dependencies=[require_permission("time.entry.write")],
)
async def update_entry(
    entry_id: uuid.UUID,
    payload: TimeEntryUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TimeEntryRead:
    entry = await TimeService(ctx).update(entry_id, payload)
    return TimeEntryRead.model_validate(entry)


@router.delete(
    "/entries/{entry_id}",
    status_code=204,
    dependencies=[require_permission("time.entry.write")],
)
async def delete_entry(
    entry_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TimeService(ctx).delete(entry_id)
