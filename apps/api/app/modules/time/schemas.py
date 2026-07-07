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
    created_at: datetime
    updated_at: datetime


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
