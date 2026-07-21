"""Pydantic schemas for the time module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class TimeEntryTypeBase(BaseModel):
    """A tenant-configurable time-entry type (#176) — the contact-types shape."""

    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    label_i18n: dict[str, str] = Field(default_factory=dict)
    position: int = 0
    active: bool = True


class TimeEntryTypeCreate(TimeEntryTypeBase):
    pass


class TimeEntryTypeUpdate(BaseModel):
    label_i18n: dict[str, str] | None = None
    position: int | None = None
    active: bool | None = None


class TimeEntryTypeRead(TimeEntryTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class TimeEntryBase(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    #: Optional link to the subscription these hours are worked under; must belong to the
    #: entry's client (the service derives the client from it when none is picked).
    subscription_id: uuid.UUID | None = None
    description: str | None = None
    #: Optional key into the org's time-entry types (#176); NULL stays untyped.
    entry_type_key: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
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
    subscription_id: uuid.UUID | None = None
    description: str | None = None
    entry_type_key: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
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
    #: The interaction this entry was logged from (#175), when it still exists.
    interaction_id: uuid.UUID | None = None
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
    """Omzet = billable minutes × the logging employee's effective rate (#226: personal
    rate → org default; entries whose logger has no rate contribute nothing)."""

    year: int
    months_current: list[float]  # index 0 = January
    months_previous: list[float]
    total_current: float
    total_previous: float
    top_clients: list[ClientRevenue]  # ordered by revenue desc (selected year)
    other_revenue: float  # everything outside the top 10


class ProjectCost(BaseModel):
    """A project's logged time in money (#111): Σ minutes × the employee's effective rate
    (#113: personal rate → org default). Since #226 that same rate prices billing too, so
    ``billable_amount`` (the billable subset — what those hours bill the client) rides along.
    ``unrated_minutes`` counts time by people with no rate at all — reported rather than
    silently priced at zero."""

    project_id: uuid.UUID
    cost: float
    billable_amount: float
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


class TimeEntryDraftPayload(BaseModel):
    """What the entry form holds mid-typing (#44). A validated, closed shape — every field
    optional (a draft may be invalid), every key whitelisted (``extra=\"forbid\"``), strings
    bounded so the row can't balloon. UI-only fields (``duration_text``) ride along verbatim."""

    model_config = ConfigDict(extra="forbid")

    date: str | None = Field(default=None, max_length=10)
    start: str | None = Field(default=None, max_length=5)
    end: str | None = Field(default=None, max_length=5)
    break_minutes: int | None = Field(default=None, ge=0, le=1440)
    duration_text: str | None = Field(default=None, max_length=40)
    billable: bool | None = None
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    subscription_id: uuid.UUID | None = None
    description: str | None = Field(default=None, max_length=4000)


class TimeEntryDraftRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    entry_date: date
    payload: dict
    updated_at: datetime


class DayView(BaseModel):
    """A single day's entries (the calendar day view) with totals."""

    date: date
    total_minutes: int
    billable_minutes: int
    entries: list[TimeEntryRead]
    #: The caller's own autosaved draft for this day (#44); never someone else's.
    draft: TimeEntryDraftRead | None = None


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
    #: Days this week where the caller has an autosaved draft (#44) — the tab dots.
    draft_days: list[date] = Field(default_factory=list)
