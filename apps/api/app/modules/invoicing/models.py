"""``invoicing`` — native invoices & quotes (issue #207, CLAUDE.md §6).

The decisions the issue demanded, encoded where they bite:

- **Tax is tenant data, locale-seeded, never law in code** (the ``leave_holidays`` rule):
  ``invoicing_tax_rates`` is seeded from a per-country generator and freely edited; a line
  **snapshots** the rate it was priced at (``tax_rate_pct`` + ``tax_name``), so re-rating a
  tax later never reprices an issued document — the actor-snapshot rule (#64), applied to
  money.
- **Numbers are assigned at issue, not at draft.** ``number`` is NULL on drafts; issuing
  allocates from the per-org sequence on ``invoicing_settings`` under a row lock. A partial
  unique index keeps issued numbers unique per org without making drafts fight over NULL.
- **Statuses never contradict** (UX): invoice ``draft → open → paid`` (+ ``cancelled``);
  *overdue* is **derived** (open + past due), never stored. Quote ``draft → open →
  accepted/rejected/expired → invoiced``.
- **Cross-module links carry no FK** (§6): ``subscription_id`` and ``time_entry_id`` are
  plain UUIDs validated through published surfaces — the tables stay decoupled, and a
  subscription deleted later never cascades into a ledgered document.
- **The CRM is not the ledger** (#31): ``invoicing_external_refs`` remembers what an
  accounting package knows so an export can be idempotent; the books live over there.
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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.customfields import CustomizableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class InvoiceStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    PAID = "paid"
    CANCELLED = "cancelled"


class InvoiceKind(StrEnum):
    INVOICE = "invoice"
    CREDIT_NOTE = "credit_note"


class QuoteStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    INVOICED = "invoiced"


class TaxCategory(StrEnum):
    """How a rate behaves on a document — vocabulary, not law. ``REVERSE_CHARGE`` prints its
    mandatory notice and charges 0; ``EXEMPT`` charges nothing and reports nothing. What a
    tenant calls them, and which exist at all, is their data."""

    STANDARD = "standard"
    REDUCED = "reduced"
    ZERO = "zero"
    EXEMPT = "exempt"
    REVERSE_CHARGE = "reverse_charge"


class InvoicingSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: seller identity, numbering sequences, defaults, reminder policy.

    The **seller block** (``company_details``) is what every document header and UBL export
    prints about the agency itself — org_settings is branding, this is legal identity.
    Sequences live here (not in a counter table) so allocation is one ``SELECT … FOR UPDATE``
    on a row every issue path already reads.
    """

    __tablename__ = "invoicing_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_invoicing_settings_org"),)

    #: Seller identity: {name, address_line1, address_line2, postal_code, city, country,
    #: vat_number, coc_number, iban, email, phone} — validated by the schema, rendered on
    #: documents, required (name) before anything can be issued.
    company_details: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    #: ISO 3166-1 alpha-2 the org's tax lives in; picks the tax-rate seed set (#207) and the
    #: suggested treatment of foreign customers. Never hardcodes law — seeds are editable.
    tax_country: Mapped[str] = mapped_column(
        String(2), nullable=False, default="NL", server_default="NL"
    )
    #: Whether entered unit prices carry tax already (consumer-style) or not (B2B-style).
    #: Snapshotted per document at create, so flipping the org default never re-prices drafts.
    prices_include_tax: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    default_due_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=14, server_default="14"
    )
    quote_valid_days: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, server_default="30"
    )
    default_tax_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoicing_tax_rates.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    default_template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoicing_templates.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    #: Fallback rate for invoice-from-time when no project/entry rate applies.
    default_hourly_rate: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)

    # --- numbering ------------------------------------------------------------- #
    #: Format strings with {year} {yy} {seq} tokens; {seq:N} zero-pads to N digits.
    invoice_number_format: Mapped[str] = mapped_column(
        String(60), nullable=False, default="{year}-{seq:4}", server_default="{year}-{seq:4}"
    )
    quote_number_format: Mapped[str] = mapped_column(
        String(60), nullable=False, default="Q{year}-{seq:4}", server_default="Q{year}-{seq:4}"
    )
    invoice_next_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    quote_next_seq: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    #: The org-local year the sequences currently count in; a new year resets them to 1
    #: when ``number_reset_yearly`` — bookkeeping-style numbering (2026-0001).
    invoice_seq_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quote_seq_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    number_reset_yearly: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )

    # --- reminders (issue #207: automatic, opt-in) ------------------------------ #
    #: Nothing emails a client until the tenant flips this on.
    reminders_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Days past due each reminder fires at, e.g. [7, 14, 30] — the schedule is tenant
    #: config, the cron just walks it. len() bounds how often a client can ever be mailed.
    reminder_days: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=lambda: [7, 14, 30], server_default=text("'[7, 14, 30]'")
    )


class TaxRate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-defined tax rate (BTW hoog / TVA réduite / VAT zero …).

    Seeded per country by ``taxseeds.py`` — derived data like the Dutch holidays (§14), so a
    2027 rate change is a tenant edit (or a new seed row), never a code release. Documents
    snapshot the pct+name at line write; this row is only the *picker* entry, which is what
    makes deactivating or re-rating always safe.
    """

    __tablename__ = "invoicing_tax_rates"

    #: Per-locale display labels ({"nl": "21% hoog", "en": "21% standard"}) — tenant data.
    label_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    rate: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    category: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxCategory.STANDARD.value
    )
    #: The jurisdiction it belongs to (informational; used by the seeder and Boekhouding).
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    #: Ledger/VAT code an accounting export maps this rate onto (Exact/SnelStart grootboek
    #: or btw-code) — the tenant's bookkeeper fills it; UBL uses category when absent.
    ledger_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class Product(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A default product/service the tenant sells (owner request): a named line preset —
    description, unit, unit price, tax rate — that the line editor drops onto a document
    with one pick. The document line still snapshots everything it copies, so re-pricing a
    product never rewrites an issued invoice (the tax-rate discipline)."""

    __tablename__ = "invoicing_products"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The line description the pick fills in; empty = use the name.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoicing_tax_rates.id", ondelete="SET NULL"),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DocumentTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A named document design (issue #207) — org-wide, like every template (UX §5).

    ``config`` is a validated blob (schemas.TemplateConfig): accent color (NULL = the
    tenant's brand color — branding stays runtime, Golden Rule 4), logo/column toggles, and
    **per-locale** intro/footer/payment texts, which is what makes a document render in the
    customer's language while the org works in its own.
    """

    __tablename__ = "invoicing_templates"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class _DocumentColumns:
    """What an invoice and a quote share: the addressee, the money context, the design.

    A mixin of columns, not a base table — two documents, one engine (#207). ``company_id``
    cascades like every attachable (#30's stance: the CRM is not the ledger; the paper trail
    survives in ``activity_log``, which carries no FK on purpose).
    """

    number: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: Bill-to snapshot, frozen at issue: {name, address_line1, …, vat_number, email}.
    #: A company that moves later never rewrites a document already sent (#64's rule).
    customer: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    #: Units of org currency per unit of document currency; NULL = same currency. Reporting
    #: multiplies by this; the document itself stays entirely in its own currency.
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(14, 6), nullable=True)
    #: The language this document renders in — per document, defaulting from the org (§8).
    locale: Mapped[str] = mapped_column(String(10), nullable=False, default="nl")
    #: Customer reference / PO number, printed verbatim.
    reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    intro: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Snapshot of the org's prices_include_tax at create (flippable per document).
    prices_include_tax: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Totals are **computed by the service** from the lines on every write — a client sends
    # lines, never totals (issue #48's rule: the API is the authority on the number).
    subtotal: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Invoice(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    _DocumentColumns,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "invoices"
    __entity_type__ = "invoice"  # customizable (§13) + auditable (§16)

    __table_args__ = (
        Index("ix_invoices_custom", "custom", postgresql_using="gin"),
        # Issued numbers are unique per org; drafts (NULL) don't contend.
        Index(
            "uq_invoices_org_number",
            "org_id",
            "number",
            unique=True,
            postgresql_where=text("number IS NOT NULL"),
        ),
        # One invoice per subscription period — the idempotency that makes a re-run of the
        # cycle cron (or a resumed crash) unable to double-bill a client (#31's hard rule).
        Index(
            "uq_invoices_subscription_period",
            "org_id",
            "subscription_id",
            "period_end",
            unique=True,
            postgresql_where=text("subscription_id IS NOT NULL"),
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InvoiceKind.INVOICE.value
    )
    #: The invoice a credit note corrects — same-module FK, survives as NULL if it goes.
    credit_for_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=InvoiceStatus.DRAFT.value, index=True
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoicing_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: Provenance: the quote this invoice was converted from (plain UUID — the FK direction
    #: lives on ``quotes.invoice_id``; two mutual FKs would tie table creation in a knot).
    quote_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: The agreement this bills a period of (#30) — cross-module, so no FK (§6).
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Sum of registered payments, maintained by the payment writes (list pages read it
    #: without an aggregate); outstanding = total − paid_total.
    paid_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- reminder bookkeeping (issue #207) ----------------------------------- #
    reminder_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_reminder_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Per-invoice mute — the client called, the amount is disputed, stop mailing them.
    reminders_paused: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class Quote(
    UUIDPrimaryKeyMixin,
    OrgScopedMixin,
    TimestampMixin,
    _DocumentColumns,
    CustomizableMixin,
    AuditableMixin,
    Base,
):
    __tablename__ = "quotes"
    __entity_type__ = "quote"

    __table_args__ = (
        Index("ix_quotes_custom", "custom", postgresql_using="gin"),
        Index(
            "uq_quotes_org_number",
            "org_id",
            "number",
            unique=True,
            postgresql_where=text("number IS NOT NULL"),
        ),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QuoteStatus.DRAFT.value, index=True
    )
    #: Past this date an open quote reads (and the cron marks it) expired.
    valid_until: Mapped[date | None] = mapped_column(Date, nullable=True)
    template_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoicing_templates.id", ondelete="SET NULL"),
        nullable=True,
    )
    #: The invoice this quote became. SET NULL is the safety net; the service also reverts
    #: the status to ``accepted`` when that draft invoice is deleted.
    invoice_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The customer's words when accepting/rejecting — worth keeping verbatim.
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class _LineColumns:
    """One priced line. ``tax_rate_pct``/``tax_name`` are snapshots taken when the line is
    written (resolved in the document's locale) — the picker row may change or die, the
    document keeps saying what it said."""

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    description: Mapped[str] = mapped_column(String(512), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=1)
    #: Free-form unit label ("uur", "stuk", "mnd") — printed, never computed with.
    unit: Mapped[str | None] = mapped_column(String(20), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    tax_rate_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoicing_tax_rates.id", ondelete="SET NULL"),
        nullable=True,
    )
    tax_rate_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False, default=0)
    tax_name: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    tax_category: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaxCategory.STANDARD.value
    )
    #: quantity × unit_price, rounded once — in entered terms (incl/excl per the document).
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)


class InvoiceLine(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, _LineColumns, Base):
    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class QuoteLine(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, _LineColumns, Base):
    __tablename__ = "quote_lines"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class InvoicePayment(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A registered (partial) payment. The invoice flips to ``paid`` when the sum covers the
    total; deleting one reopens it. Negative amounts model a refund/correction."""

    __tablename__ = "invoice_payments"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    paid_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    method: Mapped[str] = mapped_column(String(30), nullable=False, default="bank")
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)


class InvoiceTimeEntry(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Which time entries an invoice billed (issue #207).

    The time module's ``invoiced_at`` says *that* an entry is billed; this says *where* — so
    deleting or cancelling a draft invoice can un-bill exactly its own entries and nothing
    else. ``time_entry_id`` is a bare UUID (§6): validated through the time module's table
    on write, never FK-coupled to it.
    """

    __tablename__ = "invoice_time_entries"
    __table_args__ = (
        # An hour can only ever be on one invoice.
        UniqueConstraint("org_id", "time_entry_id", name="uq_invoice_time_entries_entry"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    time_entry_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)


class ExternalRef(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """What an accounting provider knows about a local record (#31's `external_refs`, shipped
    with the seam instead of the first OAuth provider).

    The unique key is the idempotency rule: a retried export finds its ref and updates,
    never creates a second external document. ``payload`` holds provider bookkeeping
    (sync hash, remote state) without a schema commitment.
    """

    __tablename__ = "invoicing_external_refs"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "provider", "local_type", "local_id",
            name="uq_invoicing_external_refs_local",
        ),
    )

    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    local_type: Mapped[str] = mapped_column(String(20), nullable=False)  # invoice | company
    local_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
