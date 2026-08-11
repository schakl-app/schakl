"""Pydantic schemas for the leave module (CLAUDE.md §6, §9, §14)."""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.leave.models import (
    AvailabilityChange,
    AvailabilityKind,
    EmploymentKind,
    LeaveCalendarDisplay,
    LeaveRequestStatus,
)
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
    # Types sharing this present as one employee-facing balance (#265). None = standalone.
    balance_group: str | None = Field(default=None, max_length=50, pattern=r"^[a-z0-9_]+$")
    # Roostervrij/ADV (#65): entitlement is the scheduled−contract hours gap, not default_weeks.
    accrues_schedule_gap: bool = False
    # How the agenda draws this type's absences (#270): a full-day chip, or an hour block.
    calendar_display: LeaveCalendarDisplay = LeaveCalendarDisplay.ALL_DAY
    position: int = 0
    active: bool = True


class LeaveTypeCreate(LeaveTypeBase):
    pass


#: The ``leave_types`` columns that are genuinely nullable, where an explicit ``null``
#: *clears* the value and must keep working. Everything else on the table is ``NOT NULL``.
_CLEARABLE_TYPE_FIELDS = frozenset({"default_weeks", "carry_over_months", "balance_group"})


class LeaveTypeUpdate(BaseModel):
    label_i18n: dict[str, str] | None = None
    color: str | None = Field(default=None, max_length=20)
    paid: bool | None = None
    tracks_balance: bool | None = None
    requires_approval: bool | None = None
    default_weeks: Decimal | None = Field(default=None, ge=0, le=52)
    carry_over_months: int | None = Field(default=None, ge=0, le=120)
    balance_group: str | None = Field(default=None, max_length=50, pattern=r"^[a-z0-9_]+$")
    accrues_schedule_gap: bool | None = None
    calendar_display: LeaveCalendarDisplay | None = None
    position: int | None = None
    active: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _reject_explicit_nulls(cls, data: Any) -> Any:
        """``None`` here means "not supplied", so an explicit ``null`` is a client error.

        Every field above is ``| None`` because that is what makes it optional on the wire
        (the generated client turns a defaulted, non-nullable property into a *required*
        one). The cost is that a literal ``{"calendar_display": null}`` used to travel all
        the way to a ``NOT NULL`` column and surface as a 500 — the shape ``exclude_unset``
        already expresses by omission, so nothing is lost by refusing it at the edge and
        answering 422 through the standard envelope instead.
        """
        if isinstance(data, dict):
            offenders = [
                key
                for key, value in data.items()
                if value is None and key in cls.model_fields and key not in _CLEARABLE_TYPE_FIELDS
            ]
            if offenders:
                raise ValueError(f"errors.null_not_allowed: {', '.join(sorted(offenders))}")
        return data


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
    #: May approvers decide/edit/backdate their own leave (#110)? Off = separation of duties;
    #: the org's sole approver may always self-manage regardless.
    self_approval: bool = False
    #: Look-ahead for the rostered-free-day generator (#107), for open-ended contracts; a
    #: fixed-term contract is always filled to its end date instead.
    recurring_horizon_months: int = 12
    #: The house default hourly rate (#113); the per-employee rate (#82) overrides it.
    default_hourly_rate: Decimal | None = None


class LeaveSettingsUpdate(BaseModel):
    """A **partial** update: only the fields present in the body are written.

    The schedule screen and the holiday screen both save here, and a full replace would let
    whichever one shipped first quietly reset the other's settings to their defaults.
    """

    default_schedule: WorkSchedule | None = None
    holiday_country: str | None = Field(default=None, max_length=2)
    holiday_auto_import: bool | None = None
    self_approval: bool | None = None
    #: Bounded: below a month the monthly cron outruns it; past two years is planning fiction.
    recurring_horizon_months: int | None = Field(default=None, ge=1, le=24)
    #: Explicit ``null`` clears the default (#113); bounded like the per-employee rate.
    default_hourly_rate: Decimal | None = Field(default=None, ge=0, le=Decimal("100000"))


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


# --- employment contracts (#65) ------------------------------------------------ #


class EmploymentContractBase(BaseModel):
    start_date: date
    #: ``null`` = open-ended (still employed). Termination = setting this later.
    end_date: date | None = None
    #: Payroll or a freelance engagement. Defaulted rather than required, so every existing
    #: caller — the wizard before this change, an import, an MCP tool — keeps writing employees.
    employment_type: EmploymentKind = EmploymentKind.EMPLOYEE
    #: The legal contract hours — entered, never derived from the schedule. ``null`` is "no fixed
    #: weekly commitment" and is only accepted on a freelance period (the service refuses it on an
    #: employee one); the bound stays ``gt=0`` because *zero* agreed hours is not a thing anybody
    #: means to type — an absent commitment is absent, not nought.
    contract_hours_per_week: Decimal | None = Field(default=None, gt=0, le=Decimal("80"))
    #: This period's working week; ``null`` follows the profile (legacy) / org default.
    schedule: WorkSchedule | None = None
    #: Free time accrued per week, or ``null`` to derive ``max(0, norm − contract hours)``.
    #: ``0`` says the free time is already in the roster — see the model docstring for why that
    #: has to be sayable per contract.
    free_time_hours_per_week: Decimal | None = Field(default=None, ge=0, le=Decimal("80"))
    note: str | None = None


class EmploymentContractCreate(EmploymentContractBase):
    user_id: uuid.UUID


class EmploymentContractUpdate(BaseModel):
    """Correcting or terminating a contract. A *changed* contract is a new row, not an edit.

    Every field is optional, and the service reads ``model_fields_set`` rather than testing for
    ``None``: on ``schedule`` and ``free_time_hours_per_week`` an explicit ``null`` is a value
    ("inherit the week", "derive the free time"), not an omission.
    """

    start_date: date | None = None
    end_date: date | None = None
    employment_type: EmploymentKind | None = None
    #: An explicit ``null`` clears the agreed hours ("no fixed weekly commitment"), which the
    #: service accepts only on a period that is — or is becoming — freelance.
    contract_hours_per_week: Decimal | None = Field(default=None, gt=0, le=Decimal("80"))
    schedule: WorkSchedule | None = None
    free_time_hours_per_week: Decimal | None = Field(default=None, ge=0, le=Decimal("80"))
    note: str | None = None


class EmploymentContractRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    start_date: date
    end_date: date | None
    employment_type: EmploymentKind
    #: ``null`` on a freelance period with no fixed weekly commitment.
    contract_hours_per_week: Decimal | None
    #: Derived from this period's week — the rostered hours the contract hours are read against.
    scheduled_hours_per_week: Decimal
    schedule: WorkSchedule | None
    #: ``null`` = derived; see :attr:`EmploymentContractBase.free_time_hours_per_week`.
    free_time_hours_per_week: Decimal | None
    #: What this contract actually accrues per week, derived rule applied — so a client renders
    #: the effective figure without re-implementing the fallback (the same shape as the hourly
    #: rate's ``effective_hourly_rate``, #113).
    effective_free_time_per_week: Decimal
    note: str | None
    created_at: datetime
    updated_at: datetime


# --- availability (freelance) ---------------------------------------------------- #


class AvailabilityBase(BaseModel):
    """One dated bend in the base week. See :class:`~app.modules.leave.models.
    EmploymentAvailability` for why this is not a leave request."""

    kind: AvailabilityKind
    #: The day it applies to; when it repeats, the first one, and its weekday is the cadence's.
    date: date
    #: The window; both omitted = the whole day. A one-sided window is resolved against the day
    #: itself, exactly as a leave request's is (#48): "from 13:00" on an 08:30–17:00 day means
    #: 13:00–17:00, not an error.
    start_time: Clock | None = None
    end_time: Clock | None = None
    #: ``1`` = every week, ``2`` = every other week, … ``null`` = this date only.
    repeat_weeks: int | None = Field(default=None, ge=1, le=8)
    #: Last date a repeat may land on; ``null`` = open-ended.
    repeat_until: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> AvailabilityBase:
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time >= self.end_time
        ):
            raise ValueError("errors.leave_availability_window_invalid")
        if self.repeat_until is not None:
            # Refused rather than ignored: a bound with no cadence is somebody believing they
            # limited a repeat that was never going to happen twice (the #300 family of bug —
            # a control that posts a value nothing reads).
            if self.repeat_weeks is None:
                raise ValueError("errors.leave_availability_repeat_required")
            if self.repeat_until < self.date:
                raise ValueError("errors.leave_availability_until_invalid")
        return self


class AvailabilityCreate(AvailabilityBase):
    #: ``null`` = the calling user. Anyone else needs ``leave.availability.write:any``.
    user_id: uuid.UUID | None = None


class AvailabilityUpdate(BaseModel):
    """Every field optional; the service reads ``model_fields_set``, so an explicit ``null`` on
    a window or a repeat clears it rather than reading as an omission."""

    kind: AvailabilityKind | None = None
    # ``dt.date``, not ``date``: the field above binds the name ``date`` to ``None`` in this
    # class namespace, and a later ``date | None`` annotation would then evaluate to ``None |
    # None``. Pydantic raises at import, so this is a build break rather than a lurking one —
    # but only in the model that gives the field a default.
    date: dt.date | None = None
    start_time: Clock | None = None
    end_time: Clock | None = None
    repeat_weeks: int | None = Field(default=None, ge=1, le=8)
    repeat_until: dt.date | None = None
    note: str | None = None


class AvailabilityMove(BaseModel):
    """"Not Tuesday, Thursday instead" — the two rows a move is, written in one act.

    The times apply to the day being *added*; the day being dropped goes whole, because that is
    what a move means. A repeat moves both halves together.
    """

    user_id: uuid.UUID | None = None
    from_date: date
    to_date: date
    start_time: Clock | None = None
    end_time: Clock | None = None
    repeat_weeks: int | None = Field(default=None, ge=1, le=8)
    repeat_until: date | None = None
    note: str | None = None

    @model_validator(mode="after")
    def _check(self) -> AvailabilityMove:
        if self.from_date == self.to_date:
            raise ValueError("errors.leave_availability_move_same_day")
        if (
            self.start_time is not None
            and self.end_time is not None
            and self.start_time >= self.end_time
        ):
            raise ValueError("errors.leave_availability_window_invalid")
        if self.repeat_until is not None:
            if self.repeat_weeks is None:
                raise ValueError("errors.leave_availability_repeat_required")
            if self.repeat_until < max(self.from_date, self.to_date):
                raise ValueError("errors.leave_availability_until_invalid")
        return self


class AvailabilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    kind: AvailabilityKind
    date: date
    start_time: Clock | None
    end_time: Clock | None
    repeat_weeks: int | None
    repeat_until: date | None
    #: Shared by the two halves of a move; ``null`` on a standalone row.
    pair_id: uuid.UUID | None
    note: str | None
    created_at: datetime
    updated_at: datetime


class AvailabilityWindow(BaseModel):
    """A stretch of one day somebody is available for."""

    start: Clock
    end: Clock


class AvailabilityDay(BaseModel):
    """What one person's one day resolves to — the base week with every exception applied.

    Computed, never stored: the base week is the period in force and the exceptions bend it, so
    there is no generated occurrence to drift from the rule that produced it.
    """

    user_id: uuid.UUID
    #: Snapshot-free: the live account, for a calendar chip that has to name somebody. The same
    #: shape the absence feed carries (``TeamLeaveItem.user_name``).
    user_name: str = ""
    date: date
    #: The stretches worked, breaks already removed. Empty = not available that day at all.
    windows: list[AvailabilityWindow]
    hours: Decimal
    #: What the untouched week would have given — the other half of every "this day changed"
    #: claim. Without it a reader cannot tell an added Saturday from a shortened Monday.
    base_hours: Decimal = Decimal(0)
    #: Which way the day moved: ``added`` (the week worked none of it), ``removed`` (all of it is
    #: gone), ``changed`` (both non-zero and different), or ``None`` for a day that resolves to
    #: exactly what the week already said.
    #:
    #: Decided here rather than by each client, for #312's reason: two surfaces re-deriving the
    #: same comparison are two surfaces that can disagree about it. It is also **not** the same
    #: question as ``deviates`` — an exception that changes nothing (a whole-day ``extra`` on a
    #: day already worked) deviates and yet changed no hours, and a calendar drawing that would
    #: be announcing a difference nobody made.
    change: AvailabilityChange | None = None
    #: The resolved day as an **instant pair** — the first window's start to the last window's
    #: end, in the org zone — for a grid that positions blocks by hour (#270). ``None`` when the
    #: day resolves to nothing. The hull, not each window: an ordinary day is two stretches
    #: either side of lunch, and "available 08:30–17:00" is what a working day means.
    #:
    #: Resolved server-side, like every other wall-clock → instant in this module (§8): the org
    #: zone lives here, so a block still starts at 08:30 on the two days a year the clocks move.
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    #: Whether any exception touched this day — a superset of ``change`` (see above).
    deviates: bool = False
    #: The exception rows behind ``deviates``, so a client can offer to undo the right one.
    entry_ids: list[uuid.UUID] = Field(default_factory=list)


# --- recurring rostered free days / ADV (#107) ---------------------------------- #


class LeaveRecurringDayBase(BaseModel):
    #: The first free day; its weekday is the pattern's weekday.
    anchor_date: date
    #: Every week (1), every other week (2), … Bounded: a cadence past 8 weeks is a
    #: hand-planned day, not a roster. Ignored — and overwritten with the nearest equivalent —
    #: when ``days_per_year`` is set.
    interval_weeks: int = Field(default=1, ge=1, le=8)
    #: **Spread mode**: this many free days a year on the anchor's weekday, placed evenly, instead
    #: of a fixed cadence. ``None`` = interval mode. Capped at 366: a "day per year" count beyond
    #: the days in one is a typo, and the balance would refuse them anyway.
    days_per_year: int | None = Field(default=None, ge=1, le=366)
    #: Part-day window ("off from 15:00"); ``None`` = the whole scheduled day (#48).
    start_time: Clock | None = None
    end_time: Clock | None = None
    note: str | None = None


class LeaveRecurringDayCreate(LeaveRecurringDayBase):
    user_id: uuid.UUID
    leave_type_id: uuid.UUID


class LeaveRecurringDayUpdate(BaseModel):
    anchor_date: date | None = None
    interval_weeks: int | None = Field(default=None, ge=1, le=8)
    #: Send ``null`` explicitly to go back to interval mode; omit to leave the mode alone.
    days_per_year: int | None = Field(default=None, ge=1, le=366)
    leave_type_id: uuid.UUID | None = None
    start_time: Clock | None = None
    end_time: Clock | None = None
    active: bool | None = None
    note: str | None = None


class LeaveRecurringDayRead(LeaveRecurringDayBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    user_id: uuid.UUID
    leave_type_id: uuid.UUID
    active: bool
    #: Days this pattern still has standing from today on. Deleting a pattern is a decision about
    #: these, so the count travels with the row rather than the UI having to go and count them.
    upcoming_days: int = 0
    created_at: datetime
    updated_at: datetime


class LeaveRecurringDeleteResult(BaseModel):
    """What deleting a pattern did — the days it took back, if it was asked to."""

    withdrawn: int = 0


class LeaveRecurringDaySaved(LeaveRecurringDayRead):
    """The saved pattern, plus how many free days the save just placed on the calendar —
    surfaced so the settings screen can confirm something visible actually happened."""

    generated: int = 0


# --- hourly rate (#82) --------------------------------------------------------- #


class LeaveRateRead(BaseModel):
    """One employee's hourly rate. ``None`` = no rate recorded (salary-adjacent, gated read)."""

    user_id: uuid.UUID
    hourly_rate: Decimal | None
    #: What cost math actually uses (#113): the employee rate, falling back to the org default.
    effective_hourly_rate: Decimal | None = None


class LeaveRateUpdate(BaseModel):
    #: Explicit ``null`` clears the rate. A rate is money, so it is bounded but never negative.
    hourly_rate: Decimal | None = Field(default=None, ge=0, le=Decimal("100000"))


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
    #: ``generated`` (re-derived on a contract change) or ``manual`` (an admin override the
    #: recompute leaves alone, #264). Lets the admin UI flag which rows are hand-set.
    source: str


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
    #: Set while an edit-bounced (previously approved) request awaits re-approval (#120).
    resubmitted_at: datetime | None = None
    #: Set when this row was generated from a recurring rostered-free-day pattern (#107).
    recurring_day_id: uuid.UUID | None = None
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
    #: The selected type, so the preview can tell the form whether saving needs (re-)approval
    #: (#72). Optional: an older client that only wants the hours can omit it.
    leave_type_id: uuid.UUID | None = None
    #: The request being edited, if any: its own hours still occupy the balance, so the
    #: over-request warning (#109) gives them back before comparing against the new span.
    request_id: uuid.UUID | None = None


class LeavePreviewResult(BaseModel):
    hours: Decimal
    #: ``hours`` in average scheduled working days — the "≈ 2 dagen" hint.
    days: Decimal
    breakdown: list[LeaveDayHours]
    #: Whether saving this span would require a manager's (re-)approval (#72): true when the
    #: chosen type requires approval, or when the span touches the past. Lets the edit form warn
    #: "saving this moves it back to pending approval" before submit. ``False`` when no type given.
    requires_approval: bool = False
    #: Whether the span reaches before today (org-local). Surfaced so the form can explain *why*
    #: an otherwise self-service edit still needs approval.
    touches_past: bool = False
    #: Remaining balance for the chosen type in the span's year, for *this* employee (the form's
    #: own balance props belong to the viewer, which differs on the register-for-someone flow).
    #: ``None`` when no type was given or the type tracks no balance. Over-requests submit; this
    #: is what lets both sides see the shortfall before they do (#109).
    remaining_hours: Decimal | None = None


# --- balances -------------------------------------------------------------------- #


class LeaveBalance(BaseModel):
    """Balance per tracks_balance type: entitled + carried − approved − pending − lapsed (#265).

    ``remaining_hours`` is expiry-aware: it reflects the FIFO-by-expiry pot ledger, so it already
    excludes carried hours that have lapsed and includes prior-year hours still in their window.
    ``balance_group`` echoes the type's group so a client can roll grouped rows into one figure —
    group remaining is exactly the sum of its types' ``remaining_hours`` by construction.
    """

    leave_type_id: uuid.UUID
    year: int
    entitled_hours: Decimal
    approved_hours: Decimal
    pending_hours: Decimal
    remaining_hours: Decimal
    #: The type's balance group (#265), or ``None`` for a standalone type.
    balance_group: str | None = None


class FreeTimeDay(BaseModel):
    """One free day on the calendar, in the shape the free-time card and the wizard both read."""

    request_id: uuid.UUID
    date: date
    hours: Decimal
    #: The window it covers, resolved — ``None``/``None`` is a whole scheduled day.
    start_time: Clock | None = None
    end_time: Clock | None = None
    #: Laid down by a pattern (as opposed to booked by hand). Only these are ever withdrawn
    #: automatically: a day the employee entered themselves is theirs to keep.
    from_pattern: bool


class FreeTimeOverview(BaseModel):
    """Everything the free-time surfaces need, in one read.

    The per-type balance answers "how many hours are left", which for free time is the *wrong*
    question and reads uselessly: once the generator has placed every day, entitled and approved
    are equal and the balance says "0 h over" — true, and no help at all in answering "when is my
    next day off". This adds the two facts that matter: which days are on the calendar, and
    whether the pot still covers them.
    """

    user_id: uuid.UUID
    year: int
    #: The active free-time types rolled into these figures (usually exactly one). Empty when the
    #: tenant deactivated free time altogether — every number below is then zero.
    leave_type_ids: list[uuid.UUID]
    entitled_hours: Decimal
    #: Booked on the calendar this year (approved + pending), whether taken yet or not.
    placed_hours: Decimal
    #: Of ``placed_hours``, the part already in the past.
    taken_hours: Decimal
    upcoming_hours: Decimal
    #: Earned but not yet on the calendar. Never negative — an excess is ``overhang_hours``.
    unplaced_hours: Decimal
    #: Placed beyond what the pot covers, which is what a contract change leaves behind (#264
    #: reprorates the entitlement; the days already on the calendar stay). The wizard reports it
    #: and offers to withdraw ``overhang`` rather than silently cancelling somebody's plans.
    overhang_hours: Decimal
    #: The employee's average scheduled day — what turns hours into "≈ 3 dagen" (§14: never
    #: ``hours_per_week / 5``).
    hours_per_day: Decimal
    next_date: date | None
    #: Upcoming free days, soonest first.
    days: list[FreeTimeDay]
    #: The future, pattern-generated subset the pot no longer covers, latest first — so
    #: withdrawing them in order gives back the most recently planned days.
    overhang: list[FreeTimeDay]


class FreeTimeWithdraw(BaseModel):
    """Withdraw specific free days. Ids, never "everything over the pot": the caller confirms a
    list it was shown, so a balance that moved in between cannot cancel more than was agreed."""

    request_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class FreeTimeWithdrawResult(BaseModel):
    cancelled: int
    #: Ids that were not cancellable (already gone, not free time, or not the caller's to touch).
    #: Reported rather than raised: one stale id should not abandon the rest of the withdrawal.
    skipped: list[uuid.UUID]


class UserLeaveBalances(BaseModel):
    user_id: uuid.UUID
    hours_per_week: Decimal
    balances: list[LeaveBalance]


# --- combined (grouped) balances (#265) ---------------------------------------- #


class LeavePotBreakdown(BaseModel):
    """One entitlement pot inside a group: which type/year it came from, and when it expires.

    The per-pot detail behind a combined figure, so "why did my balance drop by X / what is
    about to lapse" always has an answer even though the employee sees one number day to day.
    """

    leave_type_id: uuid.UUID
    accrual_year: int
    entitled_hours: Decimal
    #: What is left in this pot after FIFO-by-expiry consumption (0 once fully drawn or lapsed).
    remaining_hours: Decimal
    #: First day this pot is no longer valid (NL statutory → 1 Jul next year), or ``None`` = never.
    expires_on: dt.date | None = None
    #: True when ``expires_on`` has already passed (org-local today): these hours have lapsed.
    expired: bool = False


class LeaveGroupBalance(BaseModel):
    """The employee-facing balance for a group of pots rolled into one figure (#265).

    ``vacation_statutory`` + ``vacation_extra`` present as a single "Vakantieverlof" balance; a
    standalone type (free time, …) is its own singleton group. ``entitled/approved/pending/
    remaining`` are the combined numbers; ``pots`` carries the per-pot breakdown for anyone who
    needs it.
    """

    #: Whose balance this is — the employee the figures belong to. It matters most on the
    #: ``all_users`` roster read (#282), where one response carries every member's balance and the
    #: manager's team table keys each figure to its member. ``None`` only for a non-user-scoped
    #: caller (kept optional so older clients that never sent it still validate).
    user_id: uuid.UUID | None = None
    #: The ``balance_group`` slug, or ``None`` for a standalone (single-type) group.
    group: str | None
    #: The type ids that roll into this figure (one for a standalone group).
    leave_type_ids: list[uuid.UUID]
    #: Combined display label (group ``vacation`` → Vakantieverlof/Vacation; else the group's
    #: soonest-expiring type's own label). Per-locale, like a type's ``label_i18n``.
    label_i18n: dict[str, str]
    year: int
    entitled_hours: Decimal
    approved_hours: Decimal
    pending_hours: Decimal
    remaining_hours: Decimal
    #: Carried hours that lapsed unused (expired as of org-local today), summed over the group.
    lapsed_hours: Decimal
    #: Still-valid hours whose pot expires within the coming half year — the "use it soon" nudge.
    expiring_soon_hours: Decimal
    pots: list[LeavePotBreakdown]


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
    #: The stored times with omitted bounds resolved from the schedule (#107): "until 14:00"
    #: displays as "08:30–14:00" because a NULL start *means* the day's own start (#48).
    #: ``None`` for whole-day absences, and for a bound on an unscheduled day.
    resolved_start_time: Clock | None = None
    resolved_end_time: Clock | None = None
    #: The same window as an **instant pair**, for a calendar that positions blocks by hour
    #: (#270). A leave time is local wall clock (#48), an agenda block is an instant, and the
    #: org timezone that bridges them lives on the server — so the server does the conversion
    #: rather than every client re-deriving it and disagreeing about the last Sunday in October.
    #:
    #: Populated for **single-day** absences only, and for whole-day ones too (the scheduled
    #: day's own hours — which is the only thing an ADV day could ever be drawn at). ``None``
    #: on a multi-day span: one instant pair cannot describe Monday-to-Friday without also
    #: claiming the nights in between, and ``days`` is the honest answer for those.
    #:
    #: Reported whatever the type's ``calendar_display`` says — this is a fact about when the
    #: absence falls, and *whether* to draw it by the hour stays the client's read of the type.
    starts_at: datetime | None = None
    ends_at: datetime | None = None
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
    #: The request behind the dates, so the widget's "next leave" line opens that request rather
    #: than the year it happens to fall in (`/leave?request=<id>`, the calendar chip's deep link).
    next_leave_id: uuid.UUID | None = None
    next_leave_start: date | None
    next_leave_end: date | None
