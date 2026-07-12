"""``Hosting`` — a hosting entity a website points at (issue #93, part of #87).

Hosting is its **own** entity, not a contact: it *has* a responsible :mod:`~app.core.party`
(an employee or a technical contact) rather than *being* one. It references a catalog provider
(e.g. Cloudflare, §89) and optionally attaches to a client company so the company page can show
it. Customizable (§13), org-scoped and RLS-forced (§5).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.party import party_id_column, party_type_column
from app.db import Base


class Hosting(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    Base,
):
    __tablename__ = "hosting"
    __entity_type__ = "hosting"  # registers as customizable

    __table_args__ = (
        Index("ix_hosting_custom", "custom", postgresql_using="gin"),
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Optional attach to a client (drives the optional company panel); NULL for shared infra.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    # Wide enough for an IPv6 literal; plain text (no INET) keeps it a simple display field.
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)

    contact_party_type: Mapped[str | None] = party_type_column()
    contact_party_id: Mapped[uuid.UUID | None] = party_id_column()
