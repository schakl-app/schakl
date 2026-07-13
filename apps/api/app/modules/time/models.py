"""``TimeEntry`` — a tracked block of work (CLAUDE.md §6, §10).

Org-scoped and owned by a ``user`` (an org member / employee). A **running timer** is an entry
with ``ended_at IS NULL``; a stopped or manually-entered block has both ``ended_at`` and computed
``minutes``. Optionally attaches to a company and/or task. ``billable`` feeds capacity reporting
later; approved leave (P2) will surface here without being double-counted as a time entry (§14).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: Seeded per org, lazily on first use (#176) — the interaction-kinds pattern. ``email``
#: matches the interaction kind of the same key, so a Gmail-derived entry can carry it.
DEFAULT_ENTRY_TYPES: tuple[dict[str, Any], ...] = (
    {"key": "work", "label_i18n": {"en": "Work", "nl": "Werk"}, "position": 10},
    {"key": "email", "label_i18n": {"en": "Email", "nl": "E-mail"}, "position": 20},
)


class TimeEntryType(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-configurable kind of work a time entry represents (#176) — the contact-types
    shape: ``key + label_i18n + position + active``, CRUD under Instellingen. Optional on the
    entry (``entry_type_key``); existing entries stay untyped."""

    __tablename__ = "time_entry_types"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_time_entry_types_org_key"),)

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Per-locale labels ({"nl": ..., "en": ...}) — tenant data, like custom-field labels.
    label_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


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
    #: Optional key into the org's ``time_entry_types`` (#176) — no FK, so relabelling or
    #: removing a type never rewrites logged hours; NULL = untyped (every pre-#176 entry).
    entry_type_key: Mapped[str | None] = mapped_column(String(50), nullable=True)
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
