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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.core.currency import is_valid_currency
from app.core.numbering import format_valid
from app.modules.invoicing.models import (
    AutoInvoiceMode,
    InvoiceKind,
    InvoiceStatus,
    LineKind,
    PaymentIntentStatus,
    QuoteStatus,
    TaxCategory,
)
from app.modules.invoicing.render.engine import MAX_CUSTOM_CSS, MAX_CUSTOM_HTML


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
    #: Only a document that prints it needs it — a SEPA invoice does not, an international
    #: one often does. Off by default in the block catalog for the same reason.
    bic: str | None = Field(default=None, max_length=16)
    website: str | None = Field(default=None, max_length=255)
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
    auto_invoice_mode: AutoInvoiceMode | None = None
    reminders_enabled: bool | None = None
    reminder_days: list[int] | None = None
    #: Whether an issued invoice gets a public address at all (#304). Turning it off is
    #: retroactive — the public read checks this before it compares a token — so it withdraws
    #: links that are already on paper, which is the only useful meaning for an off switch on a
    #: credential the agency cannot collect back.
    public_invoice_links: bool | None = None

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
    auto_invoice_mode: AutoInvoiceMode
    reminders_enabled: bool
    reminder_days: list[int]
    public_invoice_links: bool = True


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
    #: The tenant's own article code (#377). Unique per org where set; blank clears it.
    code: str | None = Field(default=None, max_length=30)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Decimal = Field(default=Decimal("0"), ge=0)
    tax_rate_id: uuid.UUID | None = None
    active: bool = True
    position: int = 0

    _blank_unit = field_validator("unit", "description", "code", mode="before")(_blank_to_none)


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    code: str | None = Field(default=None, max_length=30)
    description: str | None = Field(default=None, max_length=2000)
    unit: str | None = Field(default=None, max_length=20)
    unit_price: Decimal | None = Field(default=None, ge=0)
    tax_rate_id: uuid.UUID | None = None
    active: bool | None = None
    position: int | None = None

    _blank_unit = field_validator("unit", "description", "code", mode="before")(_blank_to_none)


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
    """Which line columns the rendered document shows.

    **Superseded by** ``TemplateConfig.layout``'s ``lines`` block, which orders the columns as
    well as toggling them. Kept because every template stored before layouts existed carries
    one, and it is still the input while a template has no layout of its own — upgrading a
    release must not redesign a document a tenant has already approved. The service writes it
    back from the layout on save, so the two can never disagree.
    """

    model_config = ConfigDict(extra="forbid")

    quantity: bool = True
    unit: bool = False
    unit_price: bool = True
    tax: bool = True


class TemplateField(BaseModel):
    """One field inside a block. Position in the list is its print order."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    #: The tenant's own wording for this field's label, per locale — "t" where the catalog
    #: says "Telefoon". Empty (or a field that prints no label at all) keeps the catalog's.
    #: Bounded because a layout may hold forty of these and it lives in a JSONB column every
    #: document read touches.
    label_i18n: dict[str, str] = Field(default_factory=dict)

    @field_validator("label_i18n")
    @classmethod
    def _bounded_labels(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 12:
            raise ValueError("errors.invoicing.template_too_large")
        for locale, text in value.items():
            if len(locale) > 12 or len(text) > 60:
                raise ValueError("errors.invoicing.template_too_large")
        return {locale: text.strip() for locale, text in value.items() if text.strip()}


class TemplateBlock(BaseModel):
    """One block of the document. Position in ``layout`` is its print order.

    Both this and its fields are a **partial** statement: keys the catalog knows and this
    layout does not are resolved at their catalog position with their catalog default
    (``render.blocks.resolve_layout``). That is what lets a field added by a later release
    appear on documents whose layout was written before it existed.
    """

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=40)
    enabled: bool = True
    fields: list[TemplateField] = Field(default_factory=list, max_length=40)


class TemplateBackground(BaseModel):
    """The mark printed behind the page — a letterhead, not a watermark.

    ``file_id`` is a tenant file (the same store the logo lives in); absent, the tenant's own
    logo is used, which is what makes the letterhead design work the moment it is picked. The
    numbers are percentages of the page, and every one of them is re-clamped at render time:
    this is tenant-writable config, and an opacity of 40 would black out the text.
    """

    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    file_id: uuid.UUID | None = None
    #: Fall back to the org logo when no file of its own is set.
    use_logo: bool = True
    opacity: float = Field(default=0.04, ge=0, le=1)
    #: Width as a percentage of the page.
    scale: float = Field(default=78, ge=5, le=200)
    x: float = Field(default=50, ge=-50, le=150)
    y: float = Field(default=50, ge=-50, le=150)
    rotate: float = Field(default=0, ge=-180, le=180)
    repeat: bool = False


class TemplateConfig(BaseModel):
    """The design knobs. ``None`` accent color = the tenant's brand color at render time
    (branding is runtime, Golden Rule 4). Text blocks are per-locale dicts, so a document
    in the customer's language gets its own words, not a translation of the org's."""

    model_config = ConfigDict(extra="forbid")

    #: Which shipped design draws the document, or ``custom`` for the tenant's own ``html``.
    design: Literal["classic", "letterhead", "custom"] = "classic"
    accent_color: str | None = Field(default=None, max_length=32)
    show_logo: bool = True
    columns: TemplateColumns = Field(default_factory=TemplateColumns)
    #: Which blocks print, in which order, with which fields. Empty = the design's defaults.
    layout: list[TemplateBlock] = Field(default_factory=list, max_length=40)
    background: TemplateBackground = Field(default_factory=TemplateBackground)
    #: A tenant-authored design (``design == "custom"``): sandboxed Jinja, rendered against
    #: the same context the shipped designs get. Authoring is gated on its own permission.
    html: str | None = Field(default=None, max_length=MAX_CUSTOM_HTML)
    #: Extra CSS. On a shipped design it layers on top; on a custom one it *is* the design.
    css: str | None = Field(default=None, max_length=MAX_CUSTOM_CSS)
    #: How the payment QR is drawn (epic #269, opened up in #305).
    #:
    #: * ``brand`` — the default and the zero-config answer: the tenant's accent colour in the
    #:   modules and their logo in the middle, so the code on a client's invoice is recognisably
    #:   *theirs* rather than a generic black square. Nothing below is read.
    #: * ``plain`` — black and white, no logo. For an agency printing monochrome on a copier, or
    #:   one whose logo simply looks wrong at 7 modules across.
    #: * ``custom`` — the fields below apply.
    #:
    #: #269 deliberately shipped **no** colour picker, and the reason it gave was right about
    #: the danger and wrong about the remedy: "letting a tenant type a hex here would be
    #: offering them a way to print an invoice nobody's phone can read". What actually stops
    #: that is not the absence of a field, it is ``render/qr.readable_pair`` — which already
    #: replaced an unscannable accent and now judges the whole pair, refuses an inverted one,
    #: and is *shown* in the editor's live preview with the substitution explained. A tenant who
    #: wants their code on their own tinted panel is asking for something ordinary, and the
    #: honest answer is to let them and to keep the guarantee, not to keep the guarantee by
    #: keeping the request out.
    qr_style: Literal["brand", "plain", "custom"] = "brand"
    #: ``custom`` only. The modules' colour; ``None`` follows ``accent_color``.
    qr_color: str | None = Field(default=None, max_length=32)
    #: ``custom`` only. The panel behind them; ``None`` is paper white. Replaced together with
    #: ``qr_color`` when the pair cannot be scanned — never half-corrected.
    qr_background: str | None = Field(default=None, max_length=32)
    #: ``custom`` only. Whose mark sits in the middle: the org logo, nothing, or an image of
    #: this template's own. A logo raises the error-correction level to ``H`` wherever it comes
    #: from, so ``none`` is a genuinely denser, more forgiving code and not merely a plainer one.
    qr_logo: Literal["brand", "none", "custom"] = "brand"
    #: ``qr_logo == "custom"``: a file this org owns, uploaded through the ordinary files API
    #: exactly as the background mark is. A missing or unreadable one degrades to a plain code
    #: (``render/qr._probe_logo``), never to a hole with nothing in it.
    qr_logo_file_id: uuid.UUID | None = None
    #: The sentence under the code, per locale. Empty falls back to the built-in — "Scan om te
    #: betalen" / "…om deze factuur te bekijken", picked by whether a payment can actually be
    #: started — so a tenant who says nothing keeps the wording that already answers both cases.
    qr_caption_i18n: dict[str, str] = Field(default_factory=dict)
    #: Per-locale text blocks: {"nl": "...", "en": "..."} — shown above the lines.
    intro_i18n: dict[str, str] = Field(default_factory=dict)
    #: Below the totals: payment instructions ("Gelieve te betalen binnen {days} dagen …").
    payment_i18n: dict[str, str] = Field(default_factory=dict)
    #: Small print at the very bottom (registrations, legal footer).
    footer_i18n: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _custom_needs_a_body(self) -> TemplateConfig:
        # A custom design with nothing in it renders a blank page, which reads as data loss.
        if self.design == "custom" and not (self.html or "").strip():
            raise ValueError("errors.invoicing.template_body_required")
        return self


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


class TemplateCatalog(BaseModel):
    """What the template editor needs to draw itself. Keys only — the client owns labels."""

    blocks: list[dict[str, Any]]
    designs: list[str]
    #: Whether this caller may write ``html``/``css`` — hides the tab, never the boundary.
    can_author: bool


class TemplateSource(BaseModel):
    """A shipped design's own source, for branching a custom template off it."""

    html: str
    css: str


class TemplatePreview(BaseModel):
    """Render a sample document with a config that has not been saved."""

    config: TemplateConfig
    #: The template being edited, when there is one. It supplies the *stored* source as the
    #: baseline for the authoring check, so redrawing a saved custom template needs no
    #: `invoicing.template.author` — only changing its code does.
    template_id: uuid.UUID | None = None


class QrPreview(BaseModel):
    """The payment QR as this unsaved config would draw it (#305).

    Its own endpoint rather than a corner of the full document preview, for two reasons that
    are both about the editor being usable. A whole-document render is a WeasyPrint-adjacent
    Jinja pass on a sample invoice and takes long enough that dragging a colour picker through
    it is unpleasant; and the *code* is the one element where the tenant needs to see the
    substitution rule fire, which a 3cm square in a scaled-down A4 preview cannot show.
    """

    #: The inline ``<svg>``, ready to drop into the editor. Same encoder, same geometry and
    #: same error level as the document — one ``qr_svg`` call, so a preview that scans is a
    #: promise about the paper rather than about the preview.
    svg: str
    #: What is actually being drawn, after ``readable_pair``.
    dark: str
    light: str
    #: True when the pair asked for could not be scanned and black-on-white was substituted.
    #: The editor says so in words: the alternative is a tenant picking a colour, seeing a
    #: different one, and concluding the field is broken.
    replaced: bool = False


# --------------------------------------------------------------------------- #
# Lines & shared document pieces
# --------------------------------------------------------------------------- #
class LineWrite(BaseModel):
    description: str = Field(min_length=1, max_length=512)
    #: What this line is (§ ``LineKind``) — drives grouping on the document, never the money.
    line_kind: LineKind = LineKind.PRODUCT
    quantity: Decimal = Field(default=Decimal(1))
    unit: str | None = Field(default=None, max_length=20)
    #: May be negative: a discount line is an ordinary line with a negative price.
    unit_price: Decimal = Field(default=Decimal(0))
    tax_rate_id: uuid.UUID | None = None
    #: The unbilled time entries this line bills — so the invoice stamps exactly them and
    #: releases exactly them when the line goes. A **list**, because a grouped line ("24 uur,
    #: Project X") covers many entries; ``time_entry_id`` stays accepted as the one-entry
    #: spelling and folds into it. Validated server-side; a stale, foreign or already-billed
    #: id is silently skipped. Ignored by quotes.
    time_entry_ids: list[uuid.UUID] = Field(default_factory=list)
    #: The one-entry spelling of ``time_entry_ids``, kept so an existing caller keeps working.
    time_entry_id: uuid.UUID | None = None
    #: When a line bills a subscription period, the agreement and the period it covers — so
    #: the invoice **claims** that period and the cycle cron never bills it again (owner:
    #: "the cron should know it is already paid"). Same handling as ``time_entry_ids``:
    #: validated server-side, silently skipped when stale.
    subscription_id: uuid.UUID | None = None
    #: The same claim for a domain renewal period (#250) — a client's year-end invoice
    #: carries eleven of these next to some hours, and each one has to stop its own cron.
    domain_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None

    _blank_unit = field_validator("unit", mode="before")(_blank_to_none)

    @model_validator(mode="after")
    def _claims_are_whole(self) -> LineWrite:
        # A period without a source claims nothing; a source without a period would claim
        # *every* period. Refuse both rather than store a half-claim.
        if self.time_entry_id is not None and self.time_entry_id not in self.time_entry_ids:
            self.time_entry_ids = [*self.time_entry_ids, self.time_entry_id]
        if self.subscription_id is not None and self.domain_id is not None:
            # One line, one agreement: a claim that is both would retire two periods on a
            # single description and no reader could tell which.
            raise ValueError("errors.invoicing.one_claim_per_line")
        source = self.subscription_id or self.domain_id
        if source is not None and self.period_end is None:
            raise ValueError("errors.invoicing.subscription_period_required")
        if self.period_end is not None and source is None:
            raise ValueError("errors.invoicing.subscription_required")
        return self


class LineRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    position: int
    line_kind: LineKind
    description: str
    quantity: Decimal
    unit: str | None
    unit_price: Decimal
    tax_rate_id: uuid.UUID | None
    tax_rate_pct: Decimal
    tax_name: str
    tax_category: TaxCategory
    amount: Decimal
    #: What this line bills. Echoed so the editor can **re-post** it: the lines are replaced
    #: wholesale on every save, so a read that dropped the claim produced a write that
    #: released it, and the cron then billed the period a second time. Always empty on a
    #: quote, which claims nothing.
    time_entry_ids: list[uuid.UUID] = Field(default_factory=list)
    subscription_id: uuid.UUID | None = None
    domain_id: uuid.UUID | None = None
    period_start: date | None = None
    period_end: date | None = None


class TaxGroupRead(BaseModel):
    rate_pct: Decimal
    category: TaxCategory
    name: str
    base: Decimal
    tax: Decimal


class CustomerRead(BaseModel):
    """The bill-to snapshot on a document (frozen at issue)."""

    model_config = ConfigDict(extra="ignore")

    #: Who the document is **addressed to** — the client's legal name where they have one,
    #: else their label (``app/core/naming.py``). Resolved once, at snapshot time, so a later
    #: rename never rewrites a document that was already sent.
    name: str | None = None
    #: What the agency **calls** them (EN 16931 BT-45's trading name), carried beside it so the
    #: covering e-mail can greet a human and a document read back in two years can say which
    #: client it was without a join. ``None`` on documents issued before the split.
    trade_name: str | None = None
    address_line1: str | None = None
    address_line2: str | None = None
    postal_code: str | None = None
    city: str | None = None
    country: str | None = None
    vat_number: str | None = None
    coc_number: str | None = None
    email: str | None = None
    #: The contact the document was addressed to (*t.a.v.*), frozen with the rest.
    attn: str | None = None
    #: The client's own number in the tenant's books, so a template can print *Klantnummer*.
    client_number: str | None = None


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
    #: Only stated when it differs from the invoice date; printed by a template that asks.
    delivery_date: date | None = None
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
    delivery_date: date | None = None
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


class InvoicePaymentAccountRead(BaseModel):
    """A payment credential this org has connected, as an invoice screen needs it (#267).

    Prefixed like everything else in this module: a bare ``PaymentAccountRead`` would collide
    with the provider module's own schemas and silently qualify *both* in the generated client
    (the ``TemplateRead`` lesson). No secret and no credential — the label, the mode and
    whether it can start a payment right now.
    """

    model_config = ConfigDict(from_attributes=True)

    provider: str
    id: uuid.UUID
    label: str
    #: ``live`` or ``test``, straight from the credential. Rendered beside the label because
    #: "I thought we were live" is the one mistake this integration can make with real money.
    mode: str
    active: bool


class InvoicePaymentIntentCreate(BaseModel):
    """Start an online payment for an invoice.

    Deliberately carries **no amount**: the server charges the invoice's outstanding balance,
    recomputed at creation time. A client-supplied amount is the one thing a payment endpoint
    must never accept (#267's hard requirement).
    """

    #: Omit to use the org's only connected provider. Required as soon as there are two —
    #: nothing here ever picks a credential for you.
    provider: str | None = None
    account_id: uuid.UUID | None = None


class InvoicePaymentIntentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    invoice_id: uuid.UUID
    provider: str
    account_id: uuid.UUID | None
    status: PaymentIntentStatus
    amount: Decimal
    currency: str
    mode: str
    #: Where to send the payer. ``None`` once the provider stops offering one — an expired
    #: checkout is not a link to render.
    checkout_url: str | None
    #: What the payer chose, in the provider's own vocabulary (``ideal``, ``creditcard``).
    method: str | None
    synced_at: datetime | None
    #: When the ledger row was written. ``status="paid"`` with this ``None`` is the state a
    #: human must be shown — the money arrived and we have not booked it.
    settled_at: datetime | None
    last_error: str | None
    created_at: datetime


class InvoicePaymentRefresh(BaseModel):
    """What a "did my payment land yet?" poll answers with (#304).

    ``changed`` is whether a provider was actually asked — a throttled poll answers ``False``
    and is not a failure. The caller re-reads the document either way; this only tells a page
    whether it is worth invalidating.
    """

    changed: bool = False
    #: The latest attempt's state, so a page can stop polling without a second round-trip.
    status: PaymentIntentStatus | None = None
    settled: bool = False
    #: The document's status after the refresh — ``paid`` is what the payer came back to see.
    invoice_status: InvoiceStatus | None = None


class PublicInvoiceRead(BaseModel):
    """The invoice as a reader with **no session** sees it (#304).

    Deliberately *not* ``InvoiceRead`` and deliberately not a subset expressed as an exclusion
    list. Every field here was typed out on purpose, so the next field added to the staff model
    does not appear on an unauthenticated endpoint as a side effect of somebody extending
    something else. It carries no ids at all — not the invoice's, not the company's, not the
    contact's — because the token is the only name this surface has for anything, and an id it
    handed out would be an id somebody tries somewhere else.

    The document itself (lines, addresses, totals, the tenant's design) is not here either: the
    page shows the **rendered document**, which is the same HTML the PDF prints, fetched from
    its own route. One artefact, so a public page can never disagree with the paper.
    """

    number: str = ""
    kind: InvoiceKind = InvoiceKind.INVOICE
    status: InvoiceStatus
    issue_date: date | None = None
    due_date: date | None = None
    currency: str
    locale: str
    total: Decimal
    paid_total: Decimal
    outstanding: Decimal
    #: Who it was billed to, as frozen on the document — so the page can say "for Acme BV"
    #: and a person who opened the wrong link knows immediately.
    customer_name: str = ""
    #: A credential is connected *and* there is something to collect (#253).
    payable: bool = False
    #: The latest attempt, for the sentence a payer returning from a checkout needs to read.
    payment_status: PaymentIntentStatus | None = None
    payment_settled: bool = False
    payment_pending: bool = False


class PublicCheckout(BaseModel):
    """Where to send the payer. The provider's live checkout URL and nothing else."""

    checkout_url: str


class CreditNoteRef(BaseModel):
    """A credit note as seen from the invoice it corrects — enough to link to it and say
    how much of it that invoice absorbed."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    number: str | None = None
    status: InvoiceStatus
    total: Decimal
    applied_total: Decimal


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
    delivery_date: date | None = None
    currency: str
    exchange_rate: Decimal | None
    locale: str
    reference: str | None
    intro: str | None
    notes: str | None
    template_id: uuid.UUID | None
    quote_id: uuid.UUID | None
    subscription_id: uuid.UUID | None
    domain_id: uuid.UUID | None
    period_start: date | None
    period_end: date | None
    prices_include_tax: bool
    subtotal: Decimal
    tax_total: Decimal
    total: Decimal
    paid_total: Decimal
    #: Written off by issued credit notes (on the invoice) / absorbed by the invoice it
    #: corrects (on the credit note). What a credit note has left over is the refund.
    credited_total: Decimal = Decimal(0)
    applied_total: Decimal = Decimal(0)
    #: Derived, never stored — like ``overdue``. Payments and credit notes both take a
    #: document down; ``outstanding`` is what is left whichever way it got there.
    credited: bool = False
    fully_credited: bool = False
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
    #: Online payment attempts (#267), resolved on the **detail** read only — a list draws
    #: neither, and loading every invoice's intents to render a page of numbers is exactly
    #: the shape docs/PERFORMANCE.md forbids.
    intents: list[InvoicePaymentIntentRead] = Field(default_factory=list)
    #: Derived on the detail read: this org has an active payment credential *and* this
    #: document is in a state one could be spent on. It exists so the **portal** can decide
    #: whether to draw a "pay now" button without being allowed to read which accounts the
    #: agency has connected — a control that always refuses is a broken control (#253).
    online_payment: bool = False
    #: The document's **public** address (#304), for staff to hand to a client who rings up
    #: asking for it. Detail read only, and empty for an external login: a client is already
    #: looking at the document, and a bearer token on their own screen is a thing to forward
    #: by accident. Empty also for a draft, and for an org with public links switched off.
    public_url: str = ""
    #: Both halves of a correction, resolved on the detail read so either document can link
    #: to the other. Empty on a list, which draws the ``credited`` flag instead.
    credit_notes: list[CreditNoteRef] = Field(default_factory=list)
    credit_for_number: str = ""
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
    #: Overrides every rate for this build — employee rates and the org default alike (#226).
    hourly_rate: Decimal | None = Field(default=None, ge=0)


class SubscriptionLineOffer(BaseModel):
    description: str
    quantity: Decimal
    unit_price: Decimal


class PeriodOffer(BaseModel):
    """One outstanding billing period of one agreement — the unit the picker selects.

    A period, not an agreement: an agreement that has been paused, whose automation was off,
    or that was simply never billed owes *several*, and offering only the next one is the
    reason a user reaches for a hand-typed line. ``already_billed`` is shown rather than
    hidden, so "did I invoice March?" is answered on the picker instead of by a duplicate.
    """

    period_start: date | None
    period_end: date
    amount: Decimal
    #: The agreement's own lines, priced at *this* period's boundary; a single priced line
    #: otherwise — exactly what the cron would have raised, so both paths bill the same money.
    lines: list[SubscriptionLineOffer] = Field(default_factory=list)
    already_billed: bool = False
    #: The period has not ended yet: billing it is billing in advance, which is a choice
    #: rather than a mistake, so it is offered and labelled instead of withheld.
    future: bool = False


class BillableSubscription(BaseModel):
    """One of a client's agreements and every period of it still outstanding."""

    id: uuid.UUID
    name: str
    currency: str
    #: The agreement's current price — the header figure. Each period carries its own,
    #: resolved at that period's boundary, because history never reprices itself.
    amount: Decimal
    interval: str = ""
    periods: list[PeriodOffer] = Field(default_factory=list)
    #: More outstanding periods exist than the cap returned. Reported, never silent.
    truncated: bool = False
    #: The agreement has no billing cycle (``next_invoice_date IS NULL``), so no period can be
    #: named and none can be claimed. Surfaced as a warning rather than dropped: a paused or
    #: mis-set agreement is exactly what the user is looking for when they open the picker.
    no_cycle: bool = False


class BillableDomain(BaseModel):
    """A domain and every renewal period of it still outstanding (#250) — the subscription
    shape, one entity over. Renewals already print in a document's subscription section; a
    picker that claimed to show everything outstanding and omitted them would be lying."""

    id: uuid.UUID
    name: str
    currency: str
    amount: Decimal
    periods: list[PeriodOffer] = Field(default_factory=list)
    truncated: bool = False
    no_cycle: bool = False
    #: No price could be resolved for this domain at all (no override, no TLD price valid at
    #: the boundary). It cannot be offered as a priced line, and saying so beats a silent 0.
    no_price: bool = False
    #: This domain is not invoiced (#298): a registrar register says the agency does not hold
    #: its registration, or somebody set the flag. **Its periods are still listed**, because
    #: automation skipping a renewal and a human being forbidden to bill one are different
    #: things — the picker labels it and stays out of the way.
    invoiceable: bool = True


class UnbilledEntry(BaseModel):
    id: uuid.UUID
    started_at: datetime
    minutes: int
    description: str | None
    project_id: uuid.UUID | None
    project_name: str = ""
    user_name: str = ""
    #: The rate that would be billed for this entry: the logger's effective employee rate
    #: (#226: personal → leave org default), else the invoicing default, else 0 — the same
    #: chain ``from_time`` applies (minus a per-build override).
    rate: Decimal = Decimal(0)


class UnbilledRead(BaseModel):
    entries: list[UnbilledEntry]
    total_minutes: int
    hourly_rate: Decimal | None
    #: How many entries are outstanding in total, which is **not** ``len(entries)`` once the
    #: cap bites. The count and the money are exact whatever the cap; only the detail is cut.
    total_count: int = 0
    total_amount: Decimal = Decimal(0)
    #: The detail list was capped. Over a limit is an error or a flag, never a silent
    #: truncation that reads as "this is everything" (§17's parsing rule, applied to a read).
    truncated: bool = False


class OutstandingRead(BaseModel):
    """Everything a client still has to be invoiced for, in one round trip.

    Three buckets because the editor has three sections, and one call because the picker
    opens on all three at once: three browser fetches for one dialog is the shape
    ``docs/PERFORMANCE.md`` exists to prevent.
    """

    hours: UnbilledRead
    subscriptions: list[BillableSubscription] = Field(default_factory=list)
    domains: list[BillableDomain] = Field(default_factory=list)


#: The uninvoiced report's closed grouping vocabulary (#277) — what the data model has:
#: an entry carries a date, a company, a project and a logger, nothing more.
UninvoicedGroupBy = Literal["day", "week", "month", "year", "company", "project", "user"]


class UninvoicedGroup(BaseModel):
    """One bucket of the org-wide uninvoiced report (#277), summed server-side over the
    *whole* filtered set — never over the capped entry page."""

    #: Date buckets: an org-local ``YYYY-MM-DD`` / ``IYYY-WIW`` / ``YYYY-MM`` / ``YYYY``.
    #: Entity buckets: the row's id (empty string for "no company"/"no project").
    key: str
    #: The bucket's display name (company/project/user groupings); date buckets render
    #: their key client-side in the viewer's locale.
    label: str | None = None
    count: int
    minutes: int
    amount: Decimal


class UninvoicedReportEntry(BaseModel):
    """One backlog entry, with the group key it was bucketed under — computed by the same
    SQL expression as the subtotals, so client-side sectioning can never disagree."""

    id: uuid.UUID
    group_key: str
    started_at: datetime
    #: The org-local calendar day the entry belongs to (§8) — resolved server-side, like
    #: every bucket, so the row and its section can never disagree across DST.
    entry_date: date
    minutes: int
    description: str | None
    company_id: uuid.UUID | None
    company_name: str | None = None
    project_id: uuid.UUID | None
    project_name: str | None = None
    user_name: str | None = None
    #: The ``UnbilledEntry`` rate chain (#226), with the invoicing default folded in.
    rate: Decimal
    amount: Decimal


class UninvoicedReport(BaseModel):
    group: UninvoicedGroupBy
    groups: list[UninvoicedGroup]
    #: Capped at the request's ``limit``; ``truncated`` says so. Subtotals stay exact.
    entries: list[UninvoicedReportEntry]
    total_minutes: int
    total_amount: Decimal
    total_count: int
    truncated: bool


#: What a recurring backlog row is owed for (#302). Named for the line kind it will become,
#: because that is what the reader is choosing between on the page and on the document.
BacklogSource = Literal["subscription", "domain"]
#: ``all`` is not a source — it is the absence of a filter, and it is the default.
BacklogSourceFilter = Literal["all", "subscription", "domain"]
#: What the data model actually has to bucket by: whose it is, when it falls due, what kind
#: it is. Deliberately not the hours report's day/week/year — a period is not an event.
BacklogGroupBy = Literal["company", "month", "source"]


class RecurringBacklogItem(BaseModel):
    """One outstanding period: an agreement's month or a domain's renewal year (#302)."""

    #: ``<source>:<source_id>:<period_end>`` — the row's identity, because a period is not a
    #: record and has no id of its own. It is the *pair* that is unique (one agreement owes
    #: several months, and the claim tables are keyed the same way), so neither half alone can
    #: key a list. Deliberately the encoding ``OutstandingPicker`` already uses for its ticks.
    id: str
    source: BacklogSource
    #: The subscription's or domain's id — what the invoice line will carry as provenance.
    source_id: uuid.UUID
    name: str
    company_id: uuid.UUID | None
    company_name: str = ""
    currency: str
    period_start: date | None
    period_end: date
    amount: Decimal
    #: The period has not ended yet: billing it bills in advance, which is ordinary for a
    #: retainer and worth flagging rather than hiding.
    future: bool = False
    #: The level this agreement's cron runs at, already resolved against the org default —
    #: what it will do at its **next** boundary, never a claim about this row. Every period
    #: here has been passed by the cycle already, so none of them will bill themselves.
    auto_mode: AutoInvoiceMode


class RecurringBacklogGroup(BaseModel):
    key: str
    #: Empty for month and source buckets: the client renders those from the key in its own
    #: locale, the way the uninvoiced report's date buckets already work.
    label: str = ""
    count: int
    amount: Decimal


class BacklogSourceTotal(BaseModel):
    count: int
    amount: Decimal


class RecurringBacklogReport(BaseModel):
    """Org-wide recurring work still to invoice (#302) — the other half of "nog te
    factureren", beside :class:`UninvoicedReport`'s hours."""

    group: BacklogGroupBy
    source: BacklogSourceFilter
    #: The org's own default level, so the page can say what "follow the organisation" means
    #: without a second call to the settings endpoint.
    org_auto_invoice_mode: AutoInvoiceMode | None = None
    #: Bucketed over the filtered set and **before** the cap, so a narrowed view's subtotals
    #: are exact however long the list is.
    groups: list[RecurringBacklogGroup]
    #: Capped at the request's ``limit``; every total here is over the whole set.
    items: list[RecurringBacklogItem]
    total_count: int
    total_amount: Decimal
    #: Each source's whole-set figures, **ignoring** ``source`` — what the page's tiles are.
    #: A tile that only counted the source already selected would summarise nothing, so these
    #: are computed over everything even when the list beside them is narrowed to one.
    totals_by_source: dict[str, BacklogSourceTotal] = Field(default_factory=dict)
    truncated: bool


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
