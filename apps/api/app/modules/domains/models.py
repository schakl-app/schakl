"""``Domain`` — a domain name attached to a client (issue #90, part of #87).

A client's online infrastructure is ``domain → (optional) website → hosting``; this is the first,
manual slice. A domain belongs to exactly one client company (``company_id``) and points at
catalog providers for its registrar / DNS / (optionally) email host. "Who to contact" for the
registry and for email is a polymorphic :mod:`~app.core.party` reference (the agency by default).

Customizable (per-tenant custom fields ride along for free, §13) and org-scoped/RLS-forced (§5).
The nameserver / DNSSEC columns are populated by a later slice (#92) that queries public DNS on a
schedule; they are absent here and added by that migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.party import party_id_column, party_type_column
from app.db import Base


class DomainStatus(StrEnum):
    """Operational state of a domain. ``redirect``'s uptime/redirect webhook is a later slice."""

    ACTIVE = "active"
    REDIRECT = "redirect"
    PARKED = "parked"
    EXPIRED = "expired"
    INACTIVE = "inactive"


class Domain(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    Base,
):
    __tablename__ = "domains"
    __entity_type__ = "domain"  # registers as customizable

    __table_args__ = (
        # A tenant holds each domain name once.
        UniqueConstraint("org_id", "name", name="uq_domains_org_name"),
        Index("ix_domains_custom", "custom", postgresql_using="gin"),
    )

    name: Mapped[str] = mapped_column(String(253), nullable=False, index=True)
    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=DomainStatus.ACTIVE.value, index=True
    )

    # --- providers (catalog, §89): SET NULL so deleting a provider never deletes a domain --- #
    registrar_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    dns_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )

    # --- registry contact (party, §88) --- #
    registry_contact_party_type: Mapped[str | None] = party_type_column()
    registry_contact_party_id: Mapped[uuid.UUID | None] = party_id_column()

    # --- email --- #
    email_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    email_provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("providers.id", ondelete="SET NULL"), nullable=True
    )
    email_contact_party_type: Mapped[str | None] = party_type_column()
    email_contact_party_id: Mapped[uuid.UUID | None] = party_id_column()

    # --- nameservers + DNSSEC + MX, fetched from public DNS on a schedule (#92, #125) --- #
    nameservers: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    dnssec: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    # [{priority, exchange}] in priority order; NULL until first checked, [] = no MX.
    mx_records: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    dns_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
