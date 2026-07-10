"""``Task`` and its satellites — labels, checklists, comments, activity, templates
(CLAUDE.md §6, §10).

Org-scoped throughout. A task attaches to a company (its client overview) and/or a project
(its to-do list), and is assignable to an org member. Status/priority are small closed
vocabularies kept as strings. ``position`` is a float so the web can reorder by fractional
midpoints without renumbering. Recurrence is deliberately simple: a JSONB blob
``{freq, interval, mode}`` carried by exactly one task per chain, plus a real
``recurrence_next_run`` date column so the daily cron can query it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class RecurrenceFreq(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RecurrenceMode(StrEnum):
    # Spawn the next occurrence when this one is completed.
    AFTER_COMPLETION = "after_completion"
    # Spawn on schedule (daily cron), regardless of completion.
    SCHEDULE = "schedule"


class TemplateTrigger(StrEnum):
    MANUAL = "manual"
    COMPANY_STATUS = "company_status"


class Task(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Partial index: the daily cron only ever scans carriers with a pending next_run.
        Index(
            "ix_tasks_recurrence_next_run",
            "recurrence_next_run",
            postgresql_where=text("recurrence_next_run IS NOT NULL"),
        ),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # A task may belong to a project (a project's to-do list); SET NULL keeps the task if the
    # project is deleted. Cross-module FK by table name only — no import of the projects module.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskStatus.OPEN.value, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskPriority.NORMAL.value
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # Time budget for this task, in minutes (shown against logged time on the card).
    allocated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recurrence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    recurrence_next_run: Mapped[date | None] = mapped_column(Date, nullable=True)


class TaskLabel(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_labels"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TaskLabelLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_label_links"
    __table_args__ = (UniqueConstraint("task_id", "label_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_labels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class TaskChecklist(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_checklists"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TaskChecklistItem(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_checklist_items"

    checklist_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_checklists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TaskChecklistTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Org-wide reusable checklist (title + item titles) attachable to any task card."""

    __tablename__ = "task_checklist_templates"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    items: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class TaskLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """URL attachment on a task (briefs, docs, designs). File uploads need object storage
    and are deliberately not modelled yet."""

    __tablename__ = "task_links"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TaskComment(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_comments"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL so the thread survives a user's removal.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The author's display name at the time of writing (issue #64). The FK is SET NULL, so a
    # live join is the one thing that cannot survive the account it joins to: it hands back
    # ``None`` and the thread reads "—", as if nobody ever wrote the words. Snapshotting the
    # name is what keeps the comment attributable. The live join still wins while the account
    # exists — a rename should show through — so this is a fallback, not the display value.
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskActivity(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only audit trail; ``action`` maps to a ``tasks.activity.*`` i18n key."""

    __tablename__ = "task_activities"
    __table_args__ = (Index("ix_task_activities_task_id_created_at", "task_id", "created_at"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL actor = the system (recurrence cron, automation).
    #
    # …which is exactly why ``actor_name`` exists (issue #64). The FK is SET NULL, so deleting a
    # user rewrites their history into the system's: "Jane closed this" becomes "System closed
    # this", and no query can tell the two apart afterwards. The snapshot disambiguates them —
    # a name with no ``actor_user_id`` is a departed human, no name at all is genuinely the
    # system. Written on every ``_record``; the live join still wins while the account exists.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class TaskTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_templates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TemplateTrigger.MANUAL.value
    )
    # Company status that auto-applies this template (when trigger == company_status).
    trigger_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaskTemplateItem(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_template_items"

    template_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskPriority.NORMAL.value
    )
    # Due date of the instantiated task = application date + this many days.
    relative_due_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allocated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checklist_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Plain list of item titles; becomes a checklist on the instantiated task.
    checklist_items: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
