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
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
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


#: Statuses that renew at the registrar and therefore bill (#250): a redirected or parked
#: domain is still registered and still costs money; expired/inactive never invoice.
BILLABLE_STATUSES: tuple[str, ...] = (
    DomainStatus.ACTIVE.value,
    DomainStatus.REDIRECT.value,
    DomainStatus.PARKED.value,
)


class Domain(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "domains"
    __entity_type__ = "domain"  # customizable (§13) + auditable (§16)
    __activity_read_permission__ = "domains.domain.read"

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
    # Where a ``redirect``-status domain points. Stored as typed (a bare host is fine); NULL for
    # other statuses. Never coupled to ``status`` at the DB layer — the form only shows it there.
    redirect_url: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # --- pricing & renewal (#250) --- #
    #: When the registration (with this agency) began; anchors the yearly renewal cycle.
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    #: Everything after the first label of ``name`` ("nl", "co.uk"), stamped at write time so
    #: price resolution never reparses; NULL for a dotless name.
    tld: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    #: A per-domain price agreed outside the TLD list. Wins over the TLD price; no history —
    #: it's a rare manual override, not something invoiced retroactively at a stale rate.
    price_override: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: When the next ``domain.due`` fires; the cron advances it by a year. Derived from
    #: ``start_date`` (first anniversary still ahead) — the create form doesn't ask for it.
    next_invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    #: How far the renewal cron takes this domain's invoice on its own, overriding the org's
    #: default; ``NULL`` inherits. Distinct from anything about *renewing* the registration —
    #: this is only about the paper. The vocabulary is ``invoicing``'s ``AutoInvoiceMode``, put
    #: on the ``domain.due`` event so this module never reads invoicing's settings (§6).
    auto_invoice_mode: Mapped[str | None] = mapped_column(String(10), nullable=True)

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


class DomainTldPrice(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only per-TLD price history (#250) — ``SubscriptionPrice``'s shape applied to a
    TLD instead of an agreement. The current price for a TLD is the newest
    ``valid_from <= today``; a change appends (same-day rows are corrected in place), never
    mutates, so an invoice drafted last year keeps the number it was drafted at."""

    __tablename__ = "domain_tld_prices"
    __table_args__ = (
        UniqueConstraint("org_id", "tld", "valid_from", name="uq_domain_tld_prices_from"),
    )

    tld: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)
