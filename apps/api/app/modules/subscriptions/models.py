"""``subscriptions`` — recurring client agreements (issue #30, the retainer half of P2).

A subscription is a **revenue** concept ("this client pays €X per period for this scope"),
distinct from a project's monthly hour budget (a **capacity** concept). They link, they are
not the same row. Decisions recorded on the issue and encoded here:

- **Price history, never a mutating amount** — the price lives in append-only
  ``subscription_prices`` rows with a ``valid_from``; the amount at any past invoice date stays
  answerable, so history never reprices itself.
- **Included hours + rollover are tenant config, not code** — the rollover rule is a JSONB
  blob per subscription (mode + expiry), like leave carry-over (§14).
- **Proration is explicitly unsupported in v1** — a mid-period start invoices its first full
  period on ``next_invoice_date``; the operator adjusts the first invoice by hand.
- **No invoicing engine here** (#31 owns invoices): the cron emits ``subscription.due`` with
  the lines, and the accounting integration consumes it.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Date, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class SubscriptionStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CANCELLED = "cancelled"


class SubscriptionInterval(StrEnum):
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class Subscription(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "subscriptions"
    __entity_type__ = "subscription"  # customizable (§13) + auditable (§16)

    __table_args__ = (
        Index("ix_subscriptions_custom", "custom", postgresql_using="gin"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SubscriptionStatus.DRAFT.value, index=True
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    interval: Mapped[str] = mapped_column(
        String(20), nullable=False, default=SubscriptionInterval.MONTHLY.value
    )
    #: "every N intervals" — every 2 months is (monthly, 2).
    interval_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: When the next ``subscription.due`` fires; the cron advances it by the interval. NULL on
    #: drafts (nothing to invoice yet).
    next_invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    #: Hours of work the fee includes per period; consumption is measured against the time
    #: logged on the *linked* projects (the same aggregate every budget bar reads, #25).
    included_hours: Mapped[Decimal | None] = mapped_column(Numeric(7, 2), nullable=True)
    #: Tenant-configured rollover rule for unused included hours (§14's "config, not code"):
    #: ``{"mode": "none" | "carry", "expires_after_periods": int | null}``.
    rollover: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    notice_period_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class SubscriptionPrice(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only price history: the amount valid from a date. The current price is the
    newest ``valid_from <= today``; a change appends, never mutates, so past invoices keep
    the number they were issued at."""

    __tablename__ = "subscription_prices"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "subscription_id", "valid_from", name="uq_subscription_prices_from"
        ),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    valid_from: Mapped[date] = mapped_column(Date, nullable=False)


class SubscriptionLine(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """What the invoice says, line by line — so an invoice isn't one opaque number."""

    __tablename__ = "subscription_lines"

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=1)
    unit_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class SubscriptionLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Attach a subscription to the work it covers (projects, tasks) — the generic
    cross-module shape CLAUDE.md §6 describes; no FK into another module's table."""

    __tablename__ = "subscription_links"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "subscription_id", "entity_type", "entity_id",
            name="uq_subscription_links_target",
        ),
    )

    subscription_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(20), nullable=False)  # project | task
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
