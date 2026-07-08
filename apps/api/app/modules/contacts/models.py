"""``Contact`` — a client person, attachable to a company (CLAUDE.md §6, §14).

Contacts are the client's *people* (distinct from ``users``/memberships, who are the org's own
employees). Org-scoped and customizable (per-tenant custom fields). Carries a nullable
``company_id`` so a contact can exist unattached or belong to a company — the company detail page
composes a contacts panel via the registry, no edits to the company page required.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class Contact(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    Base,
):
    __tablename__ = "contacts"
    __entity_type__ = "contact"  # registers as customizable

    __table_args__ = (
        Index("ix_contacts_custom", "custom", postgresql_using="gin"),
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    job_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
