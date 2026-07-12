"""Pydantic schemas for the time module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TimeEntryBase(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    description: str | None = None
    billable: bool = True
    break_minutes: int = Field(default=0, ge=0, le=24 * 60)


class TimeEntryCreate(TimeEntryBase):
    """Manual entry. Supply either an ``ended_at`` (start/end) **or** a ``minutes`` duration.

    With ``ended_at``, worked minutes = (ended − started) − break. With ``minutes``, the end is
    derived (start + minutes + break). Validated in the service.
    """

    started_at: datetime
    ended_at: datetime | None = None
    minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class TimeEntryUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    description: str | None = None
    billable: bool | None = None
    break_minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    started_at: datetime | None = None
    ended_at: datetime | None = None
    minutes: int | None = Field(default=None, ge=0, le=24 * 60)


class TimerStart(TimeEntryBase):
    """Start a running timer for the current user (no end / duration yet)."""


class TimeEntryRead(TimeEntryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    minutes: int
    is_running: bool
    approved_at: datetime | None
    approved_by_user_id: uuid.UUID | None
    invoiced_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ReportTotals(BaseModel):
    """Aggregates over the full filtered report set (not just the returned page)."""

    count: int
    minutes: int
    billable_minutes: int
    approved_minutes: int
    open_minutes: int
    to_invoice_minutes: int
    invoiced_minutes: int


class TimeReport(BaseModel):
    items: list[TimeEntryRead]
    total: int
    limit: int
    offset: int
    totals: ReportTotals


class ProductivityRow(BaseModel):
    """Per-employee aggregates for the productivity report."""

    user_id: uuid.UUID
    minutes: int
    billable_minutes: int
    approved_minutes: int
    entry_count: int
    active_days: int


class ProductivityStats(BaseModel):
    date_from: date
    date_to: date
    rows: list[ProductivityRow]


class ClientRevenue(BaseModel):
    company_id: uuid.UUID | None
    revenue: float


class RevenueStats(BaseModel):
    """Omzet = billable minutes × the project's hourly rate (entries without a rated
    project contribute nothing — noted in the UI)."""

    year: int
    months_current: list[float]  # index 0 = January
    months_previous: list[float]
    total_current: float
    total_previous: float
    top_clients: list[ClientRevenue]  # ordered by revenue desc (selected year)
    other_revenue: float  # everything outside the top 10


class ProjectCost(BaseModel):
    """What a project's logged time *costs* (#111): Σ minutes × the employee's effective rate
    (#113: personal rate → org default). Distinct from revenue, which bills at the project
    rate. ``unrated_minutes`` counts time by people with no rate at all — reported rather than
    silently priced at zero."""

    project_id: uuid.UUID
    cost: float
    unrated_minutes: int


class EntryApproval(BaseModel):
    entry_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    approved: bool = True


class EntryInvoiced(BaseModel):
    entry_ids: list[uuid.UUID] = Field(min_length=1, max_length=1000)
    invoiced: bool = True


class BulkResult(BaseModel):
    updated: int


class TimeSummary(BaseModel):
    """Dashboard 'time today' widget payload."""

    date: date
    minutes: int
    running: TimeEntryRead | None


class DayView(BaseModel):
    """A single day's entries (the calendar day view) with totals."""

    date: date
    total_minutes: int
    billable_minutes: int
    entries: list[TimeEntryRead]


class LoggedSummary(BaseModel):
    """Aggregated logged time for an entity (e.g. a project or company), across the team."""

    minutes: int
    billable_minutes: int


# --- Weekly timesheet grid ------------------------------------------------- #
class TimesheetRow(BaseModel):
    company_id: uuid.UUID | None
    project_id: uuid.UUID | None
    task_id: uuid.UUID | None
    # minutes[0] = week_start .. minutes[6] = week_start + 6 days
    minutes: list[int]
    total: int


class Timesheet(BaseModel):
    week_start: date
    days: list[date]          # the 7 dates, for column headers
    rows: list[TimesheetRow]
    day_totals: list[int]     # column totals
    total: int                # grand total
