"""``Contact`` — a client person, attachable to companies (CLAUDE.md §6, §14).

Contacts are the client's *people* (distinct from ``users``/memberships, who are the org's own
employees). Org-scoped and customizable (per-tenant custom fields). A contact can be linked to
**many** companies through the ``company_contacts`` join table (``CompanyContact``), each link
carrying an ``is_primary`` flag — so a person can be the primary contact for one client and a
regular contact for another. The company detail page composes a contacts panel via the registry,
no edits to the company page required.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class ContactType(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-configurable kind of contact (issue #91): klantcontact, technisch contact, …

    The leave-types / roles shape (``label_i18n`` + ``active`` + ``position``, CRUD under
    Instellingen). The type sits on the **company↔contact link** (``CompanyContact``), not on the
    person: someone can be a client contact for one company and the technical contact for another.
    """

    __tablename__ = "contact_types"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_contact_types_org_key"),)

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Per-locale labels ({"nl": ..., "en": ...}) — tenant data, like custom-field labels.
    label_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Contact(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "contacts"
    __entity_type__ = "contact"  # registers as customizable + auditable (issue #67)

    __table_args__ = (
        Index("ix_contacts_custom", "custom", postgresql_using="gin"),
    )

    first_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class CompanyContact(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    Base,
):
    """A many-to-many link between a company and a contact (CLAUDE.md §6).

    ``is_primary`` marks the primary contact **for that company**; a partial unique index
    enforces at most one primary per ``(org_id, company_id)``.
    """

    __tablename__ = "company_contacts"

    __table_args__ = (
        UniqueConstraint(
            "org_id", "company_id", "contact_id", name="uq_company_contacts_link"
        ),
        # At most one primary contact per company (partial unique index).
        Index(
            "uq_company_contacts_primary",
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
    contact_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # The kind of contact this person is *for this company* (issue #91). SET NULL so deleting a
    # type never deletes the link — the person stays attached, just untyped.
    contact_type_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contact_types.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
