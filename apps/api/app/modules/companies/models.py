"""``Company`` — the hub every other module attaches to (CLAUDE.md §6).

Customizable (per-tenant custom fields via ``CustomizableMixin``) and org-scoped. Future
attachable types (contacts, websites, hosting, …) carry ``company_id`` + ``org_id`` and
contribute panels — no edits to the company page required.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.assignees import AssigneeLinkMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class CompanyStatus(StrEnum):
    """Client lifecycle; status transitions drive task-template automation (§6 events)."""

    LEAD = "lead"
    ONBOARDING = "onboarding"
    ACTIVE = "active"
    OFFBOARDING = "offboarding"
    ARCHIVED = "archived"


class Company(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "companies"
    __entity_type__ = "company"  # registers as customizable + auditable (issue #67)
    # The company horizon (#191) filters this model by its own pk, not a company_id column.
    __company_horizon_attr__ = "id"
    __activity_read_permission__ = "companies.company.read"  # trail read gate (audit F7)

    # GIN index on the JSONB custom-fields column (CLAUDE.md §13).
    __table_args__ = (
        Index("ix_companies_custom", "custom", postgresql_using="gin"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # Invoices routinely go to a different mailbox than the day-to-day contact person;
    # read by subscriptions/invoicing (#30), SnelStart export (#31), and PDF reports.
    invoice_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    # Billing identity (issue #11): what an invoice header and an accounting export (UBL,
    # #31/#207) need to know about the client. All optional here — "enough to invoice" is
    # judged where a document is issued, never on the company form. Issued documents
    # *snapshot* these into their own bill-to block, so a later address change can never
    # rewrite an invoice already sent.
    vat_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    coc_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    city: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # ISO 3166-1 alpha-2, like org tax country — drives which tax treatment a document
    # suggests (domestic / intra-EU reverse charge / export), never hardcodes any law.
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=CompanyStatus.ACTIVE.value, index=True
    )
    # The client's own logo (#196): a StoredFile reference (#123), never a blob column and
    # never tenant branding (Golden Rule 4 governs the *agency's* brand; this is client data).
    # SET NULL: deleting the file row simply unsets the logo.
    logo_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("files.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Mirror of the primary assignee (see ``CompanyAssignee``), kept in step on every write.
    # It is the expand half of an expand/contract migration (docs/WORKFLOW.md) and will be
    # dropped once no release reads it; write through the assignee links, not this column.
    responsible_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class CompanyAssignee(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    AssigneeLinkMixin,
    Base,
):
    """The org members working this client — one primary (verantwoordelijke), the rest assigned.

    The primary defaults down onto new projects and tasks under this company (overridable).
    A partial unique index enforces at most one primary per ``(org_id, company_id)``, exactly as
    ``company_contacts`` does for the primary contact person.
    """

    __tablename__ = "company_assignees"

    __table_args__ = (
        UniqueConstraint("org_id", "company_id", "user_id", name="uq_company_assignees_link"),
        Index(
            "uq_company_assignees_primary",
            "org_id",
            "company_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class CompanyGroup(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    """A tenant-defined set of companies (issue #191) — teams, branches, sensitive accounts.

    Groups scope **data** (which companies a membership can see), never capability — roles do
    that (§15). A company may sit in several groups; a membership's horizon is the union of
    its groups' companies. Deleting a group deletes its assignments, so visibility widens,
    never breaks. Auditable (§16): create/rename/delete and assignment changes are trail-worthy.
    """

    __tablename__ = "company_groups"
    __entity_type__ = "company_group"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_company_groups_name"),)

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    position: Mapped[int] = mapped_column(nullable=False, default=0, server_default="0")


class CompanyGroupMember(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """M2M: which companies a group contains (issue #191)."""

    __tablename__ = "company_group_members"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "group_id", "company_id", name="uq_company_group_members_link"
        ),
        Index("ix_company_group_members_group", "org_id", "group_id"),
        Index("ix_company_group_members_company", "org_id", "company_id"),
    )

    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("company_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )


class MembershipCompanyGroup(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The visibility assignment (issue #191): membership ↔ group.

    A membership with **no** rows here sees all companies (backwards compatible); with rows,
    only the union of its groups' companies. Resolved once per request in ``require_context``
    via the scope seam (``app/core/scope.py``); an owner (wildcard) is never restricted.
    """

    __tablename__ = "membership_company_groups"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "membership_id", "group_id", name="uq_membership_company_groups_link"
        ),
        Index("ix_membership_company_groups_membership", "org_id", "membership_id"),
    )

    membership_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("memberships.id", ondelete="CASCADE"),
        nullable=False,
    )
    group_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("company_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
