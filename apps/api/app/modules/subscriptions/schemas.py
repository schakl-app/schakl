"""Pydantic schemas for the subscriptions module (issue #30, CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.subscriptions.models import SubscriptionInterval, SubscriptionStatus


class SubscriptionLineWrite(BaseModel):
    description: str = Field(min_length=1, max_length=512)
    quantity: Decimal = Field(default=Decimal(1), ge=0)
    unit_amount: Decimal = Field(default=Decimal(0), ge=0)


class SubscriptionLineRead(SubscriptionLineWrite):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int


class SubscriptionLinkWrite(BaseModel):
    entity_type: Literal["project", "task"]
    entity_id: uuid.UUID


class SubscriptionLinkRead(SubscriptionLinkWrite):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class RolloverRule(BaseModel):
    """Unused included hours: tenant config, never policy in code (§14's rule, issue #30)."""

    model_config = ConfigDict(extra="forbid")

    mode: Literal["none", "carry"] = "none"
    #: With ``carry``: how many periods carried hours live, ``None`` = never expire.
    expires_after_periods: int | None = Field(default=None, ge=1, le=24)


class SubscriptionBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    status: SubscriptionStatus = SubscriptionStatus.DRAFT
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    interval: SubscriptionInterval = SubscriptionInterval.MONTHLY
    interval_count: int = Field(default=1, ge=1, le=12)
    start_date: date
    end_date: date | None = None
    next_invoice_date: date | None = None
    included_hours: Decimal | None = Field(default=None, ge=0)
    rollover: RolloverRule = Field(default_factory=RolloverRule)
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class SubscriptionCreate(SubscriptionBase):
    company_id: uuid.UUID
    #: The recurring fee per period; becomes the first price-history row (valid_from start_date).
    amount: Decimal = Field(ge=0)
    lines: list[SubscriptionLineWrite] = Field(default_factory=list)
    links: list[SubscriptionLinkWrite] = Field(default_factory=list)


class SubscriptionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    company_id: uuid.UUID | None = None
    status: SubscriptionStatus | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    interval: SubscriptionInterval | None = None
    interval_count: int | None = Field(default=None, ge=1, le=12)
    start_date: date | None = None
    end_date: date | None = None
    next_invoice_date: date | None = None
    included_hours: Decimal | None = Field(default=None, ge=0)
    rollover: RolloverRule | None = None
    notice_period_days: int | None = Field(default=None, ge=0, le=365)
    notes: str | None = None
    custom: dict[str, Any] | None = None
    #: A new price. Appends a history row (from ``amount_valid_from``, default the org-local
    #: today) — the stored history never mutates, so past invoices keep their number.
    amount: Decimal | None = Field(default=None, ge=0)
    amount_valid_from: date | None = None
    lines: list[SubscriptionLineWrite] | None = None
    links: list[SubscriptionLinkWrite] | None = None


class SubscriptionUsage(BaseModel):
    """Included-hours consumption for the *current* period — measured against the time logged
    on the linked projects (the same aggregate every budget bar reads, #25)."""

    period_start: date | None
    period_end: date | None
    included_hours: Decimal | None
    used_hours: float
    #: Positive = over the included hours. Flagged, never auto-billed (v1 decision).
    overage_hours: float


class SubscriptionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str = ""
    name: str
    status: SubscriptionStatus
    currency: str
    interval: SubscriptionInterval
    interval_count: int
    start_date: date
    end_date: date | None
    next_invoice_date: date | None
    included_hours: Decimal | None
    rollover: RolloverRule
    notice_period_days: int | None
    notes: str | None
    custom: dict[str, Any] = Field(default_factory=dict)
    #: The price valid today (from the history), and its monthly equivalent for MRR.
    amount: Decimal | None = None
    monthly_equivalent: float | None = None
    lines: list[SubscriptionLineRead] = Field(default_factory=list)
    links: list[SubscriptionLinkRead] = Field(default_factory=list)
    usage: SubscriptionUsage | None = None
    created_at: datetime
    updated_at: datetime


class PriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    amount: Decimal
    valid_from: date


class UpcomingInvoice(BaseModel):
    subscription_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str = ""
    name: str
    next_invoice_date: date
    amount: Decimal | None
    currency: str


class SubscriptionSummary(BaseModel):
    """Omzet view (#30): recurring revenue at a glance."""

    mrr: float
    arr: float
    active_count: int
    upcoming: list[UpcomingInvoice]
