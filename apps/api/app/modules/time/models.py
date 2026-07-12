"""``TimeEntry`` — a tracked block of work (CLAUDE.md §6, §10).

Org-scoped and owned by a ``user`` (an org member / employee). A **running timer** is an entry
with ``ended_at IS NULL``; a stopped or manually-entered block has both ``ended_at`` and computed
``minutes``. Optionally attaches to a company and/or task. ``billable`` feeds capacity reporting
later; approved leave (P2) will surface here without being double-counted as a time entry (§14).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class TimeEntry(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "time_entries"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    # NULL while a timer is running; set when stopped or on a manual entry.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Worked minutes = (ended_at − started_at) − break_minutes.
    minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    break_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    billable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Approval flow: a manager signs hours off (green check); approved entries are locked
    # for non-managers. ``invoiced_at`` marks billable hours as billed, so "to invoice" =
    # approved AND billable AND not invoiced.
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    invoiced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    @property
    def is_running(self) -> bool:
        return self.ended_at is None


class TimeEntryDraft(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The in-progress *new registration*, autosaved per (user, day) (#44).

    Deliberately its own table, never a ``time_entries`` row with a status: nothing that
    computes hours, approves, invoices or reports can ever see a draft, so a half-typed
    registration can never land on an invoice. The payload is JSONB because a draft is
    *allowed to be invalid* (no end time yet, a dangling project id) — typed NOT NULL columns
    would reject exactly the states being preserved. Author-only, stricter than the rest of
    the platform: the service filters ``user_id == ctx.user.id`` on every path — an admin has
    no business reading someone's keystrokes.
    """

    __tablename__ = "time_entry_drafts"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", "entry_date", name="uq_time_entry_drafts_day"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
