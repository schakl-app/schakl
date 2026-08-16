"""``Company`` — the hub every other module attaches to (CLAUDE.md §6).

Customizable (per-tenant custom fields via ``CustomizableMixin``) and org-scoped. Future
attachable types (contacts, websites, hosting, …) carry ``company_id`` + ``org_id`` and
contribute panels — no edits to the company page required.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
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

    __table_args__ = (
        # GIN index on the JSONB custom-fields column (CLAUDE.md §13).
        Index("ix_companies_custom", "custom", postgresql_using="gin"),
        # Klantnummer uniqueness, scoped to the tenant (Golden Rule 1 — a global unique index
        # would let one org's allocation collide with another's). Partial: the column is
        # nullable and an org that never numbers its clients must not contend on NULL.
        Index(
            "uq_companies_client_number",
            "org_id",
            "client_number",
            unique=True,
            postgresql_where=text("client_number IS NOT NULL"),
        ),
    )

    #: What this client is **called** — the label. Every list, picker, panel, dashboard, report,
    #: notification, Drive folder and breadcrumb in the product prints this one, and that is the
    #: point of it: "Bakkerij Jansen" is who the agency works with.
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    #: The entity a **document** is addressed to — "J. Jansen Holding B.V.". Read by exactly the
    #: surfaces where being wrong is a legal problem rather than an awkward one: an invoice's and
    #: a quote's frozen bill-to block, its UBL ``RegistrationName``, and the relation pushed to
    #: the tenant's accounting package.
    #:
    #: ``NULL`` is **inherit, not unfilled** — the label is also the legal name, which is the
    #: honest state of most clients and of every row that existed before this column. So every
    #: read goes through ``invoice_name`` (``legal_name or name``) and nothing was migrated out
    #: of ``name``: which of two names is the legal one is a fact only the agency holds, and a
    #: backfill would have had to guess it.
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Klantnummer / klantcode — the key a client list actually carries between systems, so
    #: the importer upserts on it before falling back to the name (a company can be renamed;
    #: its number does not change). Allocated from ``CompanySettings.client_number_format``
    #: on create, or typed by hand; either way unique within the org.
    client_number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    website: Mapped[str | None] = mapped_column(String(512), nullable=True)
    # E.164 (``+31612345678``), validated via ``app.core.phone`` on write (issue #256).
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # Street name only since #241 (the postcode lookup writes street and number apart);
    # pre-split rows still hold the composed "street 12" line here with ``house_number``
    # NULL, which renders identically wherever the two are joined back together.
    address_line1: Mapped[str | None] = mapped_column(String(255), nullable=True)
    house_number: Mapped[str | None] = mapped_column(String(32), nullable=True)
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
    # Audit F7's rule, which this type opted in without (#285): the trail is readable only by
    # someone who may read the record. A group's entries name the *companies* moved in and out
    # of it, and without a key here the feed fell back to the blanket ``activity.read`` that
    # every member holds — so horizon administration was legible to the people it restricts.
    __activity_read_permission__ = "companies.group.manage"
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


class CompanySettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: how this tenant numbers its clients.

    The counter lives here rather than in a counter table for the same reason invoicing's
    does: allocation is then one ``SELECT … FOR UPDATE`` on a row the create path already
    has to read. Formats use the shared ``app.core.numbering`` tokens.
    """

    __tablename__ = "company_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_company_settings_org"),)

    #: Klantnummer template — ``{year}``, ``{yy}``, ``{seq}``/``{seq:N}`` (app/core/numbering).
    #: Plain ``{seq:4}`` by default: unlike an invoice number, a client number is rarely
    #: year-stamped — a client acquired in 2024 keeps their number in 2027.
    client_number_format: Mapped[str] = mapped_column(
        String(60), nullable=False, default="{seq:4}", server_default="{seq:4}"
    )
    client_number_next_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: The org-local year the sequence counts in; only consulted when resetting yearly.
    client_number_seq_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Off by default, mirroring the format: restarting client numbers every year would make
    #: them ambiguous across years, which is the opposite of what a client number is for.
    client_number_reset_yearly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Whether a company created without an explicit number gets the next one automatically.
    #: A tenant that keeps its numbers in another system turns this off and types them.
    client_number_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
