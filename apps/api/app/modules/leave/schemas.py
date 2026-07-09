"""Pydantic schemas for the leave module (CLAUDE.md §6, §9, §14)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.leave.models import LeaveRequestStatus

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


# --- profiles (contract hours) ------------------------------------------------ #


class LeaveProfileRead(BaseModel):
    user_id: uuid.UUID
    hours_per_week: Decimal


class LeaveProfileUpdate(BaseModel):
    hours_per_week: Decimal = Field(gt=0, le=Decimal("80"))


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


class LeaveRequestCreate(BaseModel):
    leave_type_id: uuid.UUID
    start_date: date
    end_date: date
    hours: Decimal = Field(gt=0, le=Decimal("2000"))
    note: str | None = None
    # Managers may register leave for someone else (e.g. calling in sick by phone).
    user_id: uuid.UUID | None = None


class LeaveRequestUpdate(BaseModel):
    leave_type_id: uuid.UUID | None = None
    start_date: date | None = None
    end_date: date | None = None
    hours: Decimal | None = Field(default=None, gt=0, le=Decimal("2000"))
    note: str | None = None


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
    end_date: date
    hours: Decimal
    note: str | None
    status: LeaveRequestStatus
    decided_by_user_id: uuid.UUID | None
    decided_at: datetime | None
    decision_note: str | None
    created_at: datetime
    updated_at: datetime


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
    end_date: date
    hours: Decimal
    status: LeaveRequestStatus


# --- dashboard widget ---------------------------------------------------------------- #


class LeaveSummary(BaseModel):
    """My Day widget payload: own vacation balance + pending count + next approved leave."""

    year: int
    remaining_hours: Decimal
    hours_per_week: Decimal
    pending_count: int
    next_leave_start: date | None
    next_leave_end: date | None
