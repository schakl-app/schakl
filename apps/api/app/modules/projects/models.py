"""``Project`` — a client engagement that owns to-dos and carries a time/money budget.

CLAUDE.md §6, §10 (P2 agency core). Org-scoped and attachable to a **company** (the hub) via a
nullable ``company_id``. Tasks belong to a project (``tasks.project_id``); time entries will link
in Gate ③ so logged-vs-budget can be reported. Customizable (per-tenant custom fields, §13).

Budgets are planning figures, not ledger entries — stored as ``Numeric``, exposed as floats:
``budget_hours`` (capacity), ``budget_amount`` + ``hourly_rate`` (money), all optional.
``billable_default`` seeds the billable flag on new time entries; ``color`` tints the calendar.
"""

from __future__ import annotations

import uuid
from datetime import date
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.assignees import AssigneeLinkMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class ProjectStatus(StrEnum):
    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Project(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "projects"
    __entity_type__ = "project"  # registers as customizable + auditable (issue #67)

    __table_args__ = (
        Index("ix_projects_custom", "custom", postgresql_using="gin"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # Mirror of the primary assignee (see ``ProjectAssignee``), kept in step on every write.
    # Expand half of an expand/contract migration (docs/WORKFLOW.md): dropped once no release
    # reads it. Write through the assignee links, not this column.
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=ProjectStatus.ACTIVE.value, index=True
    )
    # "total": budget_hours covers the whole project; "monthly": it resets each month.
    budget_period: Mapped[str] = mapped_column(
        String(10), nullable=False, default="total", server_default="total"
    )
    billable_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    budget_hours: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    budget_amount: Mapped[float | None] = mapped_column(Numeric(12, 2), nullable=True)
    hourly_rate: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    color: Mapped[str | None] = mapped_column(String(20), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class ProjectAssignee(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    AssigneeLinkMixin,
    Base,
):
    """The org members working this project — one primary, the rest assigned.

    On create the project inherits the company's **primary** as its own primary, not the whole
    client roster: the people on an account are a superset of the people on any one project, and
    copying them all would silently assign work to everyone. The primary seeds new tasks' assignee.
    """

    __tablename__ = "project_assignees"

    __table_args__ = (
        UniqueConstraint("org_id", "project_id", "user_id", name="uq_project_assignees_link"),
        Index(
            "uq_project_assignees_primary",
            "org_id",
            "project_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
