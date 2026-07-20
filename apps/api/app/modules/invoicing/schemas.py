"""Pydantic schemas for the invoicing module (issue #207, CLAUDE.md §9).

The one rule that shapes them all: **clients send lines, never totals** (#48's rule applied
to money) — every ``*Read`` carries server-computed ``subtotal/tax_total/total`` and the
per-rate ``tax_groups``, and no ``*Write`` accepts any of them.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.core.currency import is_valid_currency
from app.modules.invoicing.models import (
    InvoiceKind,
    InvoiceStatus,
    QuoteStatus,
    TaxCategory,
)
from app.modules.invoicing.numbering import format_valid


def _blank_to_none(value: Any) -> Any:
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def _validate_currency(value: str) -> str:
    code = (value or "").upper()
    if not is_valid_currency(code):
        raise ValueError("errors.invoicing.invalid_currency")
    return code


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class SellerDetails(BaseModel):
    """The agency's own legal identity on documents — org_settings is branding, this is
    what a factuur must say about its sender."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=255)
    address_line1: str | None = Field(default=None, max_length=255)
    address_line2: str | None = Field(default=None, max_length=255)
    postal_code: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    vat_number: str | None = Field(default=None, max_length=32)
    coc_number: str | None = Field(default=None, max_length=32)
    iban: str | None = Field(default=None, max_length=42)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=40)

    _blanks = field_validator("*", mode="before")(_blank_to_none)


class InvoicingSettingsWrite(BaseModel):
    company_details: SellerDetails | None = None
    tax_country: str | None = Field(default=None, min_length=2, max_length=2)
    prices_include_tax: bool | None = None
    default_due_days: int | None = Field(default=None, ge=0, le=365)
    quote_valid_days: int | None = Field(default=None, ge=1, le=365)
    default_tax_rate_id: uuid.UUID | None = None
    default_template_id: uuid.UUID | None = None
    default_hourly_rate: Decimal | None = Field(default=None, ge=0)
    invoice_number_format: str | None = Field(default=None, max_length=60)
    quote_number_format: str | None = Field(default=None, max_length=60)
    #: Editable so a fresh instance can align with the books it takes over from; the
    #: allocator still guards uniqueness, so a rewind can only ever collide, not overwrite.
    invoice_next_seq: int | None = Field(default=None, ge=1)
    quote_next_seq: int | None = Field(default=None, ge=1)
    number_reset_yearly: bool | None = None
    reminders_enabled: bool | None = None
    reminder_days: list[int] | None = None

    @field_validator("invoice_number_format", "quote_number_format")
    @classmethod
    def _format_ok(cls, value: str | None) -> str | None:
        if value is not None and not format_valid(value):
            raise ValueError("errors.invoicing.invalid_number_format")
        return value

    @field_validator("reminder_days")
    @classmethod
    def _days_ok(cls, value: list[int] | None) -> list[int] | None:
        if value is None:
            return None
        if len(value) > 10 or any(d < 0 or d > 365 for d in value):
            raise ValueError("errors.validation")
        return sorted(set(value))


class InvoicingSettingsRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    company_details: SellerDetails
    tax_country: str
    prices_include_tax: bool
    default_due_days: int
    quote_valid_days: int
    default_tax_rate_id: uuid.UUID | None
    default_template_id: uuid.UUID | None
    default_hourly_rate: Decimal | None
    invoice_number_format: str
    quote_number_format: str
    invoice_next_seq: int
    quote_next_seq: int
    number_reset_yearly: bool
    reminders_enabled: bool
    reminder_days: list[int]


# --------------------------------------------------------------------------- #
# Tax rates
# --------------------------------------------------------------------------- #
class TaxRateBase(BaseModel):
    label_i18n: dict[str, str] = Field(default_factory=dict)
    rate: Decimal = Field(default=Decimal(0), ge=0, le=100)
    category: TaxCategory = TaxCategory.STANDARD
    country: str | None = Field(default=None, min_length=2, max_length=2)
    ledger_code: str | None = Field(default=None, max_length=50)
    is_default: bool = False
    active: bool = True
    position: int = 0

    _blank_ledger = field_validator("ledger_code", "country", mode="before")(_blank_to_none)


class TaxRateCreate(TaxRateBase):
    pass


class TaxRateUpdate(BaseModel):
    label_i18n: dict[str, str] | None = None
    rate: Decimal | None = Field(default=None, ge=0, le=100)
    category: TaxCategory | None = None
    country: str | None = Field(default=None, min_length=2, max_length=2)
    ledger_code: str | None = Field(default=None, max_length=50)
    is_default: bool | None = None
    active: bool | None = None
    position: int | None = None

    _blank_ledger = field_validator("ledger_code", "country", mode="before")(_blank_to_none)


class TaxRateRead(TaxRateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Products (owner request): default line presets for the editors
# --------------------------------------------------------------------------- #
class ProductBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    tax_rate_id: uuid.UUID | None = None
    active: bool = True
    position: int = 0

    _blank_unit = field_validator("unit", "description", mode="before")(_blank_to_none)


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Decimal | None = Field(default=None, ge=0)
    tax_rate_id: uuid.UUID | None = None
    active: bool | None = None
    position: int | None = None

    _blank_unit = field_validator("unit", "description", mode="before")(_blank_to_none)


class ProductRead(ProductBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
class TemplateColumns(BaseModel):
    """Which line columns the rendered document shows."""

    model_config = ConfigDict(extra="forbid")

    quantity: bool = True
    unit: bool = False
    unit_price: bool = True
    tax: bool = True


class TemplateConfig(BaseModel):
    """The design knobs. ``None`` accent color = the tenant's brand color at render time
    (branding is runtime, Golden Rule 4). Text blocks are per-locale dicts, so a document
    in the customer's language gets its own words, not a translation of the org's."""

    model_config = ConfigDict(extra="forbid")

    accent_color: str | None = Field(default=None, max_length=32)
    show_logo: bool = True
    columns: TemplateColumns = Field(default_factory=TemplateColumns)
    #: Per-locale text blocks: {"nl": "...", "en": "..."} — shown above the lines.
    intro_i18n: dict[str, str] = Field(default_factory=dict)
    #: Below the totals: payment instructions ("Gelieve te betalen binnen {days} dagen …").
    payment_i18n: dict[str, str] = Field(default_factory=dict)
    #: Small print at the very bottom (registrations, legal footer).
    footer_i18n: dict[str, str] = Field(default_factory=dict)


class TemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    config: TemplateConfig = Field(default_factory=TemplateConfig)
    is_default: bool = False
    active: bool = True
    position: int = 0


class TemplateCreate(TemplateBase):
    pass


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    config: TemplateConfig | None = None
    is_default: bool | None = None
    active: bool | None = None
    position: int | None = None


class TemplateRead(TemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Lines & shared document pieces
# --------------------------------------------------------------------------- #
class LineWrite(BaseModel):
    description: str = Field(min_length=1, max_length=512)
    quantity: Decimal = Field(default=Decimal(1))
    unit: str | None = Field(default=None, max_length=20)
    #: May be negative: a discount line is an ordinary line with a negative price.
    unit_price: Decimal = Field(default=Decimal(0))
    tax_rate_id: uuid.UUID | None = None
    #: When a line was prefilled from an unbilled time entry (the new-invoice form), the
    #: entry it came from — so ``InvoiceService.create`` bills exactly that entry and stamps
    #: it invoiced. Validated server-side; a stale/foreign id is silently skipped. Ignored by
    #: quotes and by line snapshotting (it is not a document-line column).
    time_entry_id: uuid.UUID | None = None

    _blank_unit = field_validator("unit", mode="before")(_blank_to_none)


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    tax_rate_id: uuid.UUID | None
    tax_rate_pct: Decimal
    tax_name: str
    tax_category: TaxCategory
    amount: Decimal


class TaxGroupRead(BaseModel):
    rate_pct: Decimal
    category: TaxCategory
    name: str
    base: Decimal
    tax: Decimal


class CustomerRead(BaseModel):
    """The bill-to snapshot on a document (frozen at issue)."""

    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    vat_number: str | None = None
    coc_number: str | None = None
    email: str | None = None


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #
class InvoiceCreate(BaseModel):
    company_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    kind: InvoiceKind = InvoiceKind.INVOICE
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    locale: str | None = Field(default=None, max_length=10)
    reference: str | None = Field(default=None, max_length=120)
    intro: str | None = None
    notes: str | None = None
    template_id: uuid.UUID | None = None
    issue_date: date | None = None
    due_date: date | None = None
    prices_include_tax: bool | None = None
    lines: list[LineWrite] = Field(default_factory=list, max_length=200)
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, value: str | None) -> str | None:
        return _validate_currency(value) if value is not None else None


class InvoiceUpdate(BaseModel):
    """Drafts edit everything; issued documents only what doesn't change the money — the
    service enforces which fields may still move after issue."""

    contact_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    locale: str | None = Field(default=None, max_length=10)
    reference: str | None = Field(default=None, max_length=120)
    intro: str | None = None
    notes: str | None = None
    template_id: uuid.UUID | None = None
    issue_date: date | None = None
    due_date: date | None = None
    prices_include_tax: bool | None = None
    reminders_paused: bool | None = None
    lines: list[LineWrite] | None = Field(default=None, max_length=200)
    custom: dict[str, Any] | None = None

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, value: str | None) -> str | None:
        return _validate_currency(value) if value is not None else None


class PaymentWrite(BaseModel):
    paid_on: date
    amount: Decimal
    method: Literal["bank", "cash", "card", "other"] = "bank"
    note: str | None = Field(default=None, max_length=255)

    _blank_note = field_validator("note", mode="before")(_blank_to_none)

    @field_validator("amount")
    @classmethod
    def _nonzero(cls, value: Decimal) -> Decimal:
        if value == 0:
            raise ValueError("errors.validation")
        return value


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    paid_on: date
    amount: Decimal
    method: str
    note: str | None
    created_at: datetime


class InvoiceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str = ""
    contact_id: uuid.UUID | None
    kind: InvoiceKind
    credit_for_id: uuid.UUID | None
    number: str | None
    customer: CustomerRead = Field(default_factory=CustomerRead)
    status: InvoiceStatus
    #: Derived, never stored: open + past due in the org's calendar (#207).
    overdue: bool = False
    issue_date: date | None
    due_date: date | None
    currency: str
    exchange_rate: Decimal | None
    locale: str
    reference: str | None
    intro: str | None
    notes: str | None
    template_id: uuid.UUID | None
    quote_id: uuid.UUID | None
    subscription_id: uuid.UUID | None
    period_start: date | None
    period_end: date | None
    prices_include_tax: bool
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    paid_total: Decimal
    outstanding: Decimal = Decimal(0)
    sent_at: datetime | None
    paid_at: datetime | None
    cancelled_at: datetime | None
    reminder_count: int
    last_reminder_at: datetime | None
    reminders_paused: bool
    custom: dict[str, Any] = Field(default_factory=dict)
    lines: list[LineRead] = Field(default_factory=list)
    tax_groups: list[TaxGroupRead] = Field(default_factory=list)
    payments: list[PaymentRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class InvoiceIssue(BaseModel):
    issue_date: date | None = None
    due_date: date | None = None


class DocumentSend(BaseModel):
    """POST /send: stamp ``sent_at`` and (by default) e-mail the document summary to the
    customer through the org's transport (#17). ``email=false`` records a send that
    happened outside the app (posted it, mailed it yourself)."""

    to: EmailStr | None = None
    message: str | None = Field(default=None, max_length=2000)
    email: bool = True


class InvoiceFromTime(BaseModel):
    """Build a draft invoice from unbilled time (approved + billable + not invoiced)."""

    company_id: uuid.UUID
    project_id: uuid.UUID | None = None
    #: Only entries started on/before this org-local date (inclusive); None = everything.
    until: date | None = None
    group_by: Literal["entry", "day", "project"] = "project"
    #: Overrides the org's default_hourly_rate for this build.
    hourly_rate: Decimal | None = Field(default=None, ge=0)


class UnbilledEntry(BaseModel):
    id: uuid.UUID
    started_at: datetime
    minutes: int
    description: str | None
    project_id: uuid.UUID | None
    project_name: str = ""
    user_name: str = ""
    #: The rate that would be billed for this entry: the project's hourly rate, else the
    #: org default, else 0 — the same chain ``from_time`` applies (minus a per-build override).
    rate: Decimal = Decimal(0)


class UnbilledRead(BaseModel):
    entries: list[UnbilledEntry]
    total_minutes: int
    hourly_rate: Decimal | None


# --------------------------------------------------------------------------- #
# Quotes
# --------------------------------------------------------------------------- #
class QuoteCreate(BaseModel):
    company_id: uuid.UUID
    contact_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    locale: str | None = Field(default=None, max_length=10)
    reference: str | None = Field(default=None, max_length=120)
    intro: str | None = None
    notes: str | None = None
    template_id: uuid.UUID | None = None
    issue_date: date | None = None
    valid_until: date | None = None
    prices_include_tax: bool | None = None
    lines: list[LineWrite] = Field(default_factory=list, max_length=200)
    custom: dict[str, Any] = Field(default_factory=dict)

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, value: str | None) -> str | None:
        return _validate_currency(value) if value is not None else None


class QuoteUpdate(BaseModel):
    contact_id: uuid.UUID | None = None
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    exchange_rate: Decimal | None = Field(default=None, gt=0)
    locale: str | None = Field(default=None, max_length=10)
    reference: str | None = Field(default=None, max_length=120)
    intro: str | None = None
    notes: str | None = None
    template_id: uuid.UUID | None = None
    issue_date: date | None = None
    valid_until: date | None = None
    prices_include_tax: bool | None = None
    lines: list[LineWrite] | None = Field(default=None, max_length=200)
    custom: dict[str, Any] | None = None

    @field_validator("currency")
    @classmethod
    def _currency_ok(cls, value: str | None) -> str | None:
        return _validate_currency(value) if value is not None else None


class QuoteDecision(BaseModel):
    note: str | None = Field(default=None, max_length=2000)

    _blank_note = field_validator("note", mode="before")(_blank_to_none)


class QuoteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    company_id: uuid.UUID
    company_name: str = ""
    contact_id: uuid.UUID | None
    number: str | None
    customer: CustomerRead = Field(default_factory=CustomerRead)
    status: QuoteStatus
    #: Derived: open + past valid_until (the cron also persists the flip, but a read never
    #: waits for a cron to tell the truth).
    expired: bool = False
    issue_date: date | None
    valid_until: date | None
    currency: str
    exchange_rate: Decimal | None
    locale: str
    reference: str | None
    intro: str | None
    notes: str | None
    template_id: uuid.UUID | None
    invoice_id: uuid.UUID | None
    prices_include_tax: bool
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    sent_at: datetime | None
    decided_at: datetime | None
    decision_note: str | None
    custom: dict[str, Any] = Field(default_factory=dict)
    lines: list[LineRead] = Field(default_factory=list)
    tax_groups: list[TaxGroupRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Summary (list header / dashboard)
# --------------------------------------------------------------------------- #
class InvoicingSummary(BaseModel):
    """Totals are in the **org currency**: foreign-currency documents convert through their
    stored exchange rate (1 when unset) — an approximation for steering, the documents
    themselves stay exact."""

    open_count: int
    open_total: float
    overdue_count: int
    overdue_total: float
    draft_count: int
    paid_this_year: float
    quotes_open_count: int
    quotes_open_total: float


class ExternalRefRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: str
    local_type: str
    local_id: uuid.UUID
    external_id: str
    synced_at: datetime | None
    payload: dict[str, Any] = Field(default_factory=dict)
