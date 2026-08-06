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
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin

# The automation level is core vocabulary, not this module's: `subscriptions` and `domains`
# each store an agreement's override, and neither may import from here (§6). Re-exported so
# this module's own readers still find it where they expect it.
from app.core.billing import AutoInvoiceMode
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


class LineKind(StrEnum):
    """What a document line *is* — the four things this platform bills for.

    An agency's invoice mixes worked hours, recurring agreements, domain renewals and one-off
    sales, and the reader has to tell them apart: "24 uur × € 95", "Hosting maart" and
    "vlotr.nl 2026–2027" answer different questions. So the kind is a **property of the
    line**, carried from wherever it was built (``from_time`` stamps hours, the subscription
    cycle stamps subscription, the renewal cron stamps domain, a product pick stamps product)
    through to the rendered document, which groups and subtotals by it.

    It is presentation and provenance, never money: totals are computed from quantity, price
    and tax exactly as before, and a tenant who wants one flat table simply keeps every line
    on the default.

    ``DOMAIN`` was folded into ``SUBSCRIPTION`` until #302, on the reasoning that a renewal is
    a recurring line and no reader had asked for the distinction. A reader has: a register of
    forty domains renewing across the year is the item an agency reconciles line by line
    against the registrar's own invoice, and burying it in the band that also holds three
    hosting retainers is what made that reconciliation a manual sort. Rows written before the
    split keep saying ``subscription`` — the kind is a snapshot (§14's #64 rule), so the
    documents a client already read do not change shape underneath them, and every read path
    treats the two as one legacy family where it has to (see ``_CLAIM_SOURCES``).
    """

    PRODUCT = "product"
    HOURS = "hours"
    SUBSCRIPTION = "subscription"
    DOMAIN = "domain"


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
    #: Last-resort rate for invoice-from-time when the logger has no employee rate (#226).
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

    # --- recurring billing ------------------------------------------------------ #
    #: How far the subscription/domain cron takes an invoice by itself (:class:`AutoInvoiceMode`).
    #: The org-wide default; an agreement may override it, because an agency that automates its
    #: hosting retainers still hand-assembles the one client whose invoices are always argued
    #: over — and per-org config cannot express a per-agreement fact (§14's rule, one entity
    #: over). ``draft`` is the seeded value: it is what every instance did before this column.
    auto_invoice_mode: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default=AutoInvoiceMode.DRAFT.value,
        server_default=text("'draft'"),
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

    # --- the public invoice link ------------------------------------------------ #
    #: Whether an issued invoice gets a **public** address at all — a link that opens the
    #: document and its pay button with no login (``app/modules/invoicing/public.py``).
    #:
    #: On by default, which is the deliberate choice and not the safe-looking one. The link is
    #: what the QR on a printed invoice has always promised: the client who received the paper
    #: can look at it and settle it. Off by default would have shipped a feature nobody
    #: discovers behind a switch nobody finds, and left every QR pointing at a sign-in screen
    #: for the majority of clients who hold no portal login. It is a switch rather than a
    #: constant because an agency whose invoices carry data they consider sensitive gets to say
    #: no — and turning it off is retroactive: the read refuses on this flag before it looks at
    #: the token, so an already-printed link stops working the moment the box is unticked.
    public_invoice_links: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
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
        # Same guarantee for domain renewals (#250): one invoice per (domain, period).
        Index(
            "uq_invoices_domain_period",
            "org_id",
            "domain_id",
            "period_end",
            unique=True,
            postgresql_where=text("domain_id IS NOT NULL"),
        ),
        # The public link's token. Unique **globally**, not per org: the token is the whole
        # address a session-less reader presents, so two tenants holding one string would make
        # "whose invoice is this?" ambiguous at the one lookup with nothing to fall back on.
        Index(
            "uq_invoices_public_token",
            "public_token",
            unique=True,
            postgresql_where=text("public_token IS NOT NULL"),
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
    #: When the goods or service were actually delivered — the *leverdatum* a Dutch invoice
    #: states when it differs from the invoice date. Nullable and printed only by a template
    #: whose layout asks for it: most invoices are dated the day they are delivered, and a
    #: field repeating the date above it is noise.
    delivery_date: Mapped[date | None] = mapped_column(Date, nullable=True)
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
    #: The domain this bills a renewal year of (#250) — cross-module, so no FK (§6).
    domain_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True, index=True
    )
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Sum of registered payments, maintained by the payment writes (list pages read it
    #: without an aggregate); outstanding = total − paid_total − credited_total.
    paid_total: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False, default=0)
    #: How much of this invoice issued credit notes have written off — the second way a
    #: balance comes down, and the reason a credited invoice leaves arrears and dunning.
    #: Positive on the invoice being corrected, allocated when the credit note is issued.
    credited_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
    #: The mirror of the above on a credit note: how much of it the invoice it corrects
    #: absorbed. Whatever the source had no room for is a refund the client is owed, which
    #: is what keeps "credit an open invoice" (nothing moves) apart from "credit a paid one"
    #: (money goes back). Frozen at issue: an allocation happens once.
    applied_total: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=0, server_default="0"
    )
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
    #: Raised and issued by the cron under ``AutoInvoiceMode.SEND``, and not yet mailed.
    #:
    #: The send is a **separate pass** (``jobs.py``) rather than part of the drafting handler,
    #: for one reason: ``run_per_org`` gives a whole org one transaction, so a later
    #: subscription raising anything would roll back an invoice whose e-mail had already left
    #: the building. A flag set inside that transaction and read by the next job is only ever
    #: read for an invoice that committed. Cleared on success, and on a structural failure
    #: that retrying cannot fix — recorded on the trail either way, never as daily noise.
    auto_send_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: The capability token in this invoice's **public** address (``/invoice/<token>``), minted
    #: when the document is issued and rotatable at any time. It is the only thing that names a
    #: document to a reader with no session, which is why it is a column and not something
    #: derived: a derived token cannot be revoked without changing a key that revokes every
    #: other invoice's link at the same time.
    #:
    #: ``NULL`` means *no public link exists* — a draft, an org that switched the feature off,
    #: or a rotation that has not been asked for yet. It is never inferred: the public read
    #: refuses a NULL rather than treating it as a wildcard, which is what stops an empty token
    #: in a URL from matching every un-linked invoice in the table.
    public_token: Mapped[str | None] = mapped_column(String(64), nullable=True)

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """The stricter rule an **external (client) login** reads invoices by (#266).

        Two narrowings, and only the first is a horizon. ``company_id`` is ``NOT NULL``, so
        the company match is the plain one the repository would build itself — there is no
        unattached invoice to exempt the way a company-less task is exempted. The second is
        the reason this clause exists at all: **a draft is invisible.** It carries no number,
        it was never sent, its money is still being edited (the post-issue lock only starts at
        issue) and the client has no relationship with it — showing one would tell them what
        the agency is about to charge before the agency has decided.

        It lives on the model, like ``Contact.__portal_horizon_clause__``, so that every path
        gives the client the same answer *by construction* rather than by several predicates
        happening to agree — the list and its total, the detail, and ``/pdf`` ``/preview``
        ``/ubl``, which all load through ``get()``. A draft that 404s on the detail and
        renders on the download is the same leak one route later (§15, #285).
        """
        return cls.company_id.in_(scope or frozenset()) & (
            cls.status != InvoiceStatus.DRAFT.value
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

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """``Invoice.__portal_horizon_clause__`` for quotes — defensive, and unused today.

        #266 deliberately left quotes out: whether a client should watch an offer's status
        before they have accepted it is a product decision nobody has made, and
        ``invoicing.quote.read`` stays staff-only, so no client reaches a quote at all. This
        exists because that is a *grant* rather than a *mechanism* — the role is freely
        editable in Instellingen → Rollen — and the day a tenant ticks it, the answer should
        already be "your own, and never our drafts" rather than the whole quote register.
        """
        return cls.company_id.in_(scope or frozenset()) & (
            cls.status != QuoteStatus.DRAFT.value
        )


class _LineColumns:
    """One priced line. ``tax_rate_pct``/``tax_name`` are snapshots taken when the line is
    written (resolved in the document's locale) — the picker row may change or die, the
    document keeps saying what it said."""

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    #: Hours / subscription / domain / product — what this line is, so the document can group
    #: and subtotal by it (see :class:`LineKind`). Snapshotted like every other line column.
    line_kind: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=LineKind.PRODUCT.value,
        server_default=text("'product'"),
    )
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
    """An invoice line, plus **what it bills** — provenance the quote line deliberately lacks.

    A quote claims nothing: it bills no hour and retires no period, so these columns live
    here rather than on ``_LineColumns``. They exist because the claim tables alone could not
    answer *which line* billed a thing, and the editor replaces lines wholesale on every
    save: without provenance on the row, re-saving a draft posted lines that had forgotten
    their claims, and the service dutifully released them (the cron then billed the period a
    second time). The line is now the record and the claim tables are rebuilt from it.

    ``time_entry_ids`` is a **list** because one line may bill many entries — ``from_time``
    groups per project or per day by design, and "24 uur — Project X" is one line over
    fourteen entries. The others are singular: a line bills one agreement's one period.
    """

    __tablename__ = "invoice_lines"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: The unbilled time entries this line bills — bare UUIDs (§6), validated through the
    #: time module's table on write. Empty for every other kind of line.
    time_entry_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    #: The agreement this line bills a period of (#30) — cross-module, so no FK (§6).
    subscription_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: The domain this line bills a renewal period of (#250) — cross-module, so no FK (§6).
    domain_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date | None] = mapped_column(Date, nullable=True)


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
    __table_args__ = (
        # **The idempotency of the whole online-payment path** (#267). A provider retries a
        # webhook until it gets a 200 — Mollie ten times over 26 hours — and two deliveries
        # can be in flight at once. An application-level "have we settled this yet?" check
        # loses that race; a partial unique index cannot. Partial because a hand-registered
        # bank transfer has no intent, and a hundred of those must not fight over NULL.
        Index(
            "uq_invoice_payments_intent",
            "org_id",
            "intent_id",
            unique=True,
            postgresql_where=text("intent_id IS NOT NULL"),
        ),
    )

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
    #: The online payment this row settles (#267), or NULL for one a human registered. A bare
    #: UUID rather than an FK on purpose: the ledger row is the durable fact and must survive
    #: its intent being pruned, exactly as ``activity_log.entity_id`` outlives its record.
    intent_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)


class PaymentIntentStatus(StrEnum):
    """Mirrors :class:`app.core.payments.PaymentStatus` — the provider's own vocabulary.

    Stored as the provider's word rather than translated into an invoicing status, because the
    two answer different questions: this says what happened at the provider, ``settled_at``
    says what we did about it. Collapsing them is how "the client paid and we never booked it"
    becomes invisible (CLAUDE.md §10, the Cloudflare drift rule, applied to money).
    """

    OPEN = "open"
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELED = "canceled"


class InvoicePaymentIntent(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One attempt to collect an invoice through a payment provider (#267).

    Deliberately **not** ``ExternalRef``. That table's unique key is
    ``(org, provider, local_type, local_id)`` — one row per local record — so a second checkout
    for the same invoice would overwrite the first's external id, and a late webhook for the
    abandoned attempt would then settle against a payment nobody made. An invoice legitimately
    has several attempts (iDEAL expires in fifteen minutes; clients abandon and retry), so the
    identity here is the **provider's** payment, not the invoice.

    ``account_id`` is a bare UUID: the credential lives in the provider's own module and
    invoicing may not know its table (§6). ``provider`` + ``external_id`` is what a callback
    resolves by, and the unique constraint on it is what makes resolving it safe.
    """

    __tablename__ = "invoice_payment_intents"
    __table_args__ = (
        # The webhook dedup key: one local row per provider payment, per tenant.
        UniqueConstraint(
            "org_id", "provider", "external_id", name="uq_invoice_payment_intents_external"
        ),
        Index("ix_invoice_payment_intents_invoice", "org_id", "invoice_id"),
        # The reconcile cron's read: everything not yet at rest, oldest first.
        Index("ix_invoice_payment_intents_open", "org_id", "status"),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    #: Which of the org's credentials opened it — a webhook must be re-fetched with the *same*
    #: credential, and an agency may legitimately hold two (a live one and a test one).
    account_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    #: The provider's own payment id (Mollie's ``tr_…``).
    external_id: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PaymentIntentStatus.OPEN.value,
        server_default=PaymentIntentStatus.OPEN.value,
    )
    #: What we asked the payer for — the invoice's *outstanding* amount at creation, frozen.
    #: Never re-derived: a partial payment registered in the meantime must not silently change
    #: what a checkout link already promised.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EUR")
    #: ``live`` or ``test``. A test payment settles nothing — see ``payments.py``.
    mode: Mapped[str] = mapped_column(
        String(10), nullable=False, default="live", server_default="live"
    )
    #: Where to send the payer. Long: providers hang session ids off it.
    checkout_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    #: The method the payer actually chose, in the provider's vocabulary. NULL until they pick.
    method: Mapped[str | None] = mapped_column(String(40), nullable=True)
    #: When the provider last told us something, however it reached us.
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: When a **poll** last asked the provider (#304) — the landing page's refresh, and only
    #: that. Its own column rather than a reuse of ``synced_at``, because the two answer
    #: different questions and conflating them broke the feature they exist for: ``synced_at``
    #: is written by the create as well, so a payer returning inside the throttle window was
    #: told "nothing to ask" about the payment they had just made. A webhook and the reconcile
    #: cron deliberately do **not** touch this: neither is a caller whose rate needs bounding,
    #: and letting them push the window forward would let a well-timed callback suppress the
    #: payer's own first poll.
    refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When *we* wrote the ledger row. Separate from ``status`` on purpose: ``paid`` with no
    #: ``settled_at`` is precisely the state a human must be shown and a retry must fix.
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The provider's own untranslatable text for the last failure. Read by a human, never put
    #: in an error envelope (§9).
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    @classmethod
    def __company_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """This row's client is its **invoice's** — failure mode (1) of #285.

        There is no ``company_id`` here, so without this the repository's column match finds
        nothing and therefore filters *nothing at all*. Every read in ``payments.py`` already
        goes through an invoice the document repository narrowed first, so this is the second
        lock rather than the first — which is exactly the arrangement #285 asks for, since the
        next read added will not remember.
        """
        return cls.invoice_id.in_(
            select(Invoice.id).where(
                Invoice.org_id == cls.org_id, Invoice.company_id.in_(scope or frozenset())
            )
        )


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


class InvoiceSubscriptionPeriod(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Which subscription periods an invoice already billed — ``invoice_time_entries`` for
    agreements (owner: *"the cron should know it is already paid"*).

    ``invoices.subscription_id`` answers the cycle cron's question only for the invoice the
    cron itself raised: one column holds one agreement and one period, while a hand-built
    invoice routinely carries three subscriptions plus some hours. So the claim on a period
    moves here, one row per (subscription, period_end), and ``on_subscription_due`` consults
    this table as well before drafting. The partial unique index on ``invoices`` stays as the
    backstop for the cron's own path.

    ``subscription_id`` is a bare UUID (§6): validated through the subscriptions service on
    write, never FK-coupled to another module's table.
    """

    __tablename__ = "invoice_subscription_periods"
    __table_args__ = (
        # One agreement, one period, one invoice — the whole point of the table.
        UniqueConstraint(
            "org_id",
            "subscription_id",
            "period_end",
            name="uq_invoice_subscription_periods_period",
        ),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)


class InvoiceDomainPeriod(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Which domain renewal periods an invoice already billed — the third claim table.

    ``invoices.domain_id`` + ``uq_invoices_domain_period`` answered the renewal cron only for
    the invoice the cron itself raised: one column holds one domain, while an agency's
    year-end invoice routinely carries eleven renewals next to some hours. So the claim moves
    here on the same shape as :class:`InvoiceSubscriptionPeriod`, and ``on_domain_due``
    consults it before drafting — which is what makes a hand-picked renewal stop the cron.
    The partial index on ``invoices`` stays as the backstop for the cron's own path.
    """

    __tablename__ = "invoice_domain_periods"
    __table_args__ = (
        # One domain, one renewal period, one invoice.
        UniqueConstraint(
            "org_id", "domain_id", "period_end", name="uq_invoice_domain_periods_period"
        ),
    )

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    domain_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date, nullable=True)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)


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
