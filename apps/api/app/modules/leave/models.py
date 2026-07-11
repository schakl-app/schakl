"""Employee PTO / leave (CLAUDE.md §14).

"Employees" are the org's users/memberships (not ``contacts``). Everything is tracked in
**hours** (matches time tracking and part-time contracts); the UI converts to days via the
employee's contract hours. No country's law is hardcoded: the Dutch system (wettelijk /
bovenwettelijk, carry-over, expiry) is expressed as tenant-editable ``leave_types`` config,
seeded with sensible Dutch defaults.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
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
    Time,
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


class LeaveSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Org-wide leave configuration: the schedule new employees inherit (#46), holidays (#47).

    One row per org. ``default_schedule`` is a :class:`~app.modules.leave.schedule.WorkSchedule`
    blob; an employee whose ``LeaveProfile.schedule`` is ``NULL`` follows it.
    """

    __tablename__ = "leave_settings"
    __table_args__ = (UniqueConstraint("org_id"),)

    default_schedule: Mapped[dict] = mapped_column(JSONB, nullable=False)
    #: Which generator seeds this tenant's calendar (``nl``), or ``NULL`` to seed nothing.
    holiday_country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    #: Import next year's holidays each December, unattended (ARQ cron, per org).
    holiday_auto_import: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class LeaveHoliday(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A day nobody works. Tenant data, seeded from a generator — never law hardcoded (§14).

    Per-org rows rather than a shared country table: RLS then applies uniformly and a tenant
    edits their own calendar without forking a global one (Golden Rule 1 stays trivially true).

    ``active`` is not decoration. Goede Vrijdag is worked at many Dutch employers and
    Bevrijdingsdag is a day off only every fifth year under a lot of CAOs, so the generator
    emits every holiday and the tenant switches off the ones they work. A re-import never
    resurrects what they turned off.

    ``key`` names the holiday across years (``koningsdag``), which is what lets a generated date
    that *moves* — Koningsdag when 27 April is a Sunday — move rather than duplicate. It is
    ``NULL`` for hand-added rows, whose ``source`` is ``manual`` and which imports never touch.
    """

    __tablename__ = "leave_holidays"
    __table_args__ = (UniqueConstraint("org_id", "date"),)

    date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    # Per-locale names ({"nl": "Tweede Kerstdag", "en": "Boxing Day"}) — tenant data, not keys.
    name_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: ``manual`` or the country code of the generator that produced it (``nl``).
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="manual")
    key: Mapped[str | None] = mapped_column(String(50), nullable=True)


class LeaveProfile(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-employee work schedule and the contract hours derived from it.

    ``hours_per_week`` is **maintained, not entered**: every schedule save recomputes it, and
    entitlements, balances and the days-equivalent keep reading it. It stays authoritative for
    a profile with no ``schedule`` — a pre-#46 part-timer on 32 h must not silently be granted
    the 40 h org default (see ``LeaveService.hours_per_week``).
    """

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
    #: This employee's own week, or ``NULL`` to follow ``LeaveSettings.default_schedule``.
    schedule: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: The employee's hourly rate in the org currency (#82) — salary-adjacent, so reading it is
    #: gated on ``leave.rate.read`` (own/any) and writing on ``leave.rate.write`` (admin), never
    #: on ``leave.profile.manage``. ``NULL`` = no rate recorded; revenue-per-employee then has
    #: nothing to multiply and simply omits this person. Money data conceptually broader than
    #: leave, but this is the one per-employee employment row, so it lives here (see issue #82).
    hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)


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

    ``hours`` is a **materialized result**, not a client input (#48): the service computes it
    from the employee's schedule minus weekends, holidays and breaks, and recomputes it on every
    edit. Balances, ``summary()``, ``generate_entitlements()`` and the timesheet all read it, so
    the column stays. A request counts against the balance of ``start_date``'s year. Approved
    leave surfaces on the timesheet without ever becoming a time entry (§14).
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
    #: ``NULL`` means "from the start of the scheduled day" / "until the end of it" — which is
    #: exactly what every pre-#48 row is, hence no backfill. Local wall-clock ``TIME``, not
    #: ``TIMESTAMPTZ``: "I'm off from 15:00" means 15:00 where the employee works, whether or
    #: not the clocks changed that weekend, and a whole day would have to invent a time.
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    #: A manager's deliberate departure from the computed hours (e.g. four hours agreed for a
    #: day the employee was not scheduled). Attributed, so "the number is wrong" has an answer.
    #: ``NULL`` — the ordinary case — means ``hours`` is exactly what ``compute_hours`` returned.
    hours_override: Mapped[Decimal | None] = mapped_column(Numeric(6, 2), nullable=True)
    hours_override_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
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
