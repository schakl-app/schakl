"""Employee PTO / leave (CLAUDE.md §14).

"Employees" are the org's users/memberships (not ``contacts``). Everything is tracked in
**hours** (matches time tracking and part-time contracts); the UI converts to days via the
employee's contract hours. No country's law is hardcoded: the Dutch system (wettelijk /
bovenwettelijk, carry-over, expiry) is expressed as tenant-editable ``leave_types`` config,
seeded with sensible Dutch defaults.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class LeaveRequestStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class LeaveType(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-configurable kind of leave (vacation, sick, unpaid, …).

    ``default_weeks`` expresses the yearly entitlement in *weeks of the employee's contract
    hours* — the Dutch statutory minimum is exactly 4 weeks, so entitlements stay proportional
    for part-timers without extra rules. ``carry_over_months`` is how many months into the
    next year unused hours stay valid (NL: 6 for statutory → July 1; 60 for extra-statutory).
    Both are informational config the tenant can change; nothing legal is hardcoded.
    """

    __tablename__ = "leave_types"
    __table_args__ = (UniqueConstraint("org_id", "key"),)

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Per-locale labels ({"nl": ..., "en": ...}) — tenant data, like custom-field labels.
    label_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Token from the shared label palette (task_labels convention); drives calendar chips.
    color: Mapped[str] = mapped_column(String(20), nullable=False, default="emerald")
    paid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Whether requests of this type deduct from a yearly balance (vacation yes; sick no).
    tracks_balance: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Sick leave is *reported*, not requested: types without approval auto-approve on create.
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    default_weeks: Mapped[Decimal | None] = mapped_column(Numeric(4, 2), nullable=True)
    carry_over_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LeaveProfile(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-employee contract hours — the hours↔days conversion and entitlement base."""

    __tablename__ = "leave_profiles"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    hours_per_week: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("40")
    )


class LeaveEntitlement(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Granted hours per user / leave type / year (incl. manual carry-over grants)."""

    __tablename__ = "leave_entitlements"
    __table_args__ = (UniqueConstraint("org_id", "user_id", "leave_type_id", "year"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    year: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False, default=Decimal("0"))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class LeaveRequest(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A leave request: members request, managers (owner/admin) decide.

    ``hours`` is the total across the date range (part days welcome). A request counts
    against the balance of ``start_date``'s year. Approved leave surfaces on the timesheet
    without ever becoming a time entry (§14 — never double-counted).
    """

    __tablename__ = "leave_requests"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leave_type_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("leave_types.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    end_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LeaveRequestStatus.PENDING.value, index=True
    )
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
