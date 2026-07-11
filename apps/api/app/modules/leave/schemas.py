"""Pydantic schemas for the leave module (CLAUDE.md §6, §9, §14)."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.leave.models import LeaveRequestStatus
from app.modules.leave.schedule import Clock, WorkSchedule

# --- leave types ------------------------------------------------------------- #


class LeaveTypeBase(BaseModel):
    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    label_i18n: dict[str, str] = Field(default_factory=dict)
    color: str = Field(default="emerald", max_length=20)
    paid: bool = True
    tracks_balance: bool = False
    requires_approval: bool = True
    # Yearly entitlement in weeks of contract hours (NL statutory minimum = 4).
    default_weeks: Decimal | None = Field(default=None, ge=0, le=52)
    # Months into the next year before carried-over hours expire (NL: 6 / 60). None = never.
    carry_over_months: int | None = Field(default=None, ge=0, le=120)
    position: int = 0
    active: bool = True


class LeaveTypeCreate(LeaveTypeBase):
    pass


class LeaveTypeUpdate(BaseModel):
    label_i18n: dict[str, str] | None = None
    color: str | None = Field(default=None, max_length=20)
    paid: bool | None = None
    tracks_balance: bool | None = None
    requires_approval: bool | None = None
    default_weeks: Decimal | None = Field(default=None, ge=0, le=52)
    carry_over_months: int | None = Field(default=None, ge=0, le=120)
    position: int | None = None
    active: bool | None = None


class LeaveTypeRead(LeaveTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# --- org settings (default work schedule, holiday config) ---------------------- #


class LeaveSettingsRead(BaseModel):
    default_schedule: WorkSchedule
    holiday_country: str | None = None
    holiday_auto_import: bool = True


class LeaveSettingsUpdate(BaseModel):
    """A **partial** update: only the fields present in the body are written.

    The schedule screen and the holiday screen both save here, and a full replace would let
    whichever one shipped first quietly reset the other's settings to their defaults.
    """

    default_schedule: WorkSchedule | None = None
    holiday_country: str | None = Field(default=None, max_length=2)
    holiday_auto_import: bool | None = None


# --- holidays (#47) ------------------------------------------------------------ #
# These models carry a field literally called ``date``, which shadows the type at class scope
# as soon as it has a default: ``date | None`` would then be evaluated as ``None | None``.
# Hence ``dt.date`` throughout this section — the JSON key stays ``date``.


class LeaveHolidayRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    date: dt.date
    name_i18n: dict[str, str]
    active: bool
    #: ``manual`` (hand-added, never touched by an import) or the generator's country code.
    source: str
    key: str | None


class LeaveHolidayCreate(BaseModel):
    date: dt.date
    name_i18n: dict[str, str] = Field(default_factory=dict)
    active: bool = True


class LeaveHolidayUpdate(BaseModel):
    date: dt.date | None = None
    name_i18n: dict[str, str] | None = None
    active: bool | None = None


class HolidayImport(BaseModel):
    year: int = Field(ge=2000, le=2100)
    #: Which generator to run. Defaults to the org's ``holiday_country``.
    country: str | None = Field(default=None, max_length=2)


class HolidayImportResult(BaseModel):
    """``created`` new rows, ``updated`` generated rows whose date moved, ``skipped`` the rest.

    A deactivated holiday counts as skipped, never resurrected; a date already occupied by a
    ``manual`` row is skipped too.
    """

    created: int
    updated: int
    skipped: int


# --- profiles (work schedule + contract hours) -------------------------------- #


class LeaveProfileRead(BaseModel):
    """The caller's **effective** profile: own schedule, else the org default.

    The browser must never merge the default itself — two clients would disagree about what a
    day is worth, and only one of them would agree with the server.
    """

    user_id: uuid.UUID
    hours_per_week: Decimal
    #: The average scheduled working day. What "≈ 2 dagen" divides by, never ``week / 5``.
    hours_per_day: Decimal
    schedule: WorkSchedule
    #: True when ``schedule`` is the org default rather than this employee's own.
    inherited: bool


class LeaveProfileSummary(BaseModel):
    """One row of the managers' roster: the employee's *own* schedule, or ``None``."""

    user_id: uuid.UUID
    hours_per_week: Decimal
    hours_per_day: Decimal
    schedule: WorkSchedule | None


class LeaveProfileUpdate(BaseModel):
    """``schedule`` is the input; ``hours_per_week`` is derived from it and stored.

    ``hours_per_week`` is still **accepted** for one release so an older ``web`` container
    keeps working (#46), and honoured only while the employee has no schedule. Once a schedule
    exists it wins and any posted ``hours_per_week`` is ignored — accepted, not rejected, so a
    stale client degrades instead of failing.
    """

    hours_per_week: Decimal | None = Field(default=None, gt=0, le=Decimal("80"))
    #: Explicit ``null`` clears the employee's own schedule → back to the org default.
    schedule: WorkSchedule | None = None


# --- entitlements -------------------------------------------------------------- #


class LeaveEntitlementUpsert(BaseModel):
    user_id: uuid.UUID
    leave_type_id: uuid.UUID
    year: int = Field(ge=2000, le=2100)
    hours: Decimal = Field(ge=0, le=Decimal("4000"))
    note: str | None = None


class LeaveEntitlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    leave_type_id: uuid.UUID
    year: int
    hours: Decimal
    note: str | None


class EntitlementGenerate(BaseModel):
    """Fill missing entitlements for a year from each type's default_weeks × contract hours."""

    year: int = Field(ge=2000, le=2100)


class GenerateResult(BaseModel):
    created: int


# --- requests ------------------------------------------------------------------ #


class LeaveRequestSpan(BaseModel):
    """The dates and times a request covers. ``None`` times mean whole scheduled days (#48)."""

    start_date: date
    #: From the start of the scheduled day when omitted; clamped into it when it falls outside.
    start_time: Clock | None = None
    end_date: date
    #: Until the end of the scheduled day when omitted; clamped likewise.
    end_time: Clock | None = None


class LeaveRequestCreate(LeaveRequestSpan):
    """``hours`` is **not** accepted. The server computes it from the schedule (#48).

    A client that could post ``hours: 100`` for one afternoon is a client the balance cannot
    trust, which is the whole reason the calculation moved here.
    """

    leave_type_id: uuid.UUID
    note: str | None = None
    # Managers may register leave for someone else (e.g. calling in sick by phone).
    user_id: uuid.UUID | None = None
    #: A manager's deliberate departure from the computed hours. Needs ``leave.request.approve``.
    hours_override: Decimal | None = Field(default=None, gt=0, le=Decimal("2000"))


class LeaveRequestUpdate(BaseModel):
    leave_type_id: uuid.UUID | None = None
    start_date: date | None = None
    start_time: Clock | None = None
    end_date: date | None = None
    end_time: Clock | None = None
    note: str | None = None
    #: Explicit ``null`` clears the override and returns the request to the computed hours.
    hours_override: Decimal | None = Field(default=None, gt=0, le=Decimal("2000"))


class LeaveRequestDecision(BaseModel):
    approved: bool
    note: str | None = None


class LeaveRequestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    leave_type_id: uuid.UUID
    start_date: date
    start_time: Clock | None
    end_date: date
    end_time: Clock | None
    hours: Decimal
    hours_override: Decimal | None
    hours_override_by_user_id: uuid.UUID | None
    note: str | None
    status: LeaveRequestStatus
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime


# --- the hour calculation (#48) ------------------------------------------------ #


class LeaveDayHours(BaseModel):
    """One day of a request. ``reason`` says *why* a day is worth nothing, so the UI can too."""

    date: dt.date
    hours: Decimal
    #: ``holiday`` | ``not_scheduled`` | ``outside_hours``, or ``None`` on an ordinary day.
    reason: str | None = None


class LeaveRequestPreview(LeaveRequestSpan):
    """What the form asks before it submits, so the number shown is the number stored."""

    user_id: uuid.UUID | None = None


class LeavePreviewResult(BaseModel):
    hours: Decimal
    #: ``hours`` in average scheduled working days — the "≈ 2 dagen" hint.
    days: Decimal
    breakdown: list[LeaveDayHours]
    #: The span begins before org-local today. Editing an approved request that touches the past
    #: re-triggers approval whatever its type (#72); the form warns before saving.
    touches_past: bool = False


# --- balances -------------------------------------------------------------------- #


class LeaveBalance(BaseModel):
    """Balance per tracks_balance type: entitled + carried − approved − pending."""

    leave_type_id: uuid.UUID
    year: int
    entitled_hours: Decimal
    approved_hours: Decimal
    pending_hours: Decimal
    remaining_hours: Decimal


class UserLeaveBalances(BaseModel):
    user_id: uuid.UUID
    hours_per_week: Decimal
    balances: list[LeaveBalance]


# --- team calendar feed ------------------------------------------------------------ #


class TeamLeaveItem(BaseModel):
    """One (approved or pending) absence for the team calendar / timesheet overlay."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    leave_type_id: uuid.UUID
    start_date: date
    start_time: Clock | None
    end_date: date
    end_time: Clock | None
    hours: Decimal
    status: LeaveRequestStatus
    #: Hours per day, from the schedule (#48). The timesheet renders these rather than spreading
    #: ``hours`` evenly, which would show 3,5 h Thursday and 3,5 h Friday for a 2 h + 5 h request.
    days: list[LeaveDayHours]


# --- dashboard widget ---------------------------------------------------------------- #


class LeaveSummary(BaseModel):
    """My Day widget payload: own vacation balance + pending count + next approved leave."""

    year: int
    remaining_hours: Decimal
    hours_per_week: Decimal
    #: The average scheduled working day — the widget's "≈ n dagen" divides by this (#46).
    hours_per_day: Decimal
    pending_count: int
    next_leave_start: date | None
    next_leave_end: date | None
