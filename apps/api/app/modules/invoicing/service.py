"""Business logic for invoicing (issue #207) — tenant-scoped throughout (Golden Rule 1).

The decisions, where they are enforced:

- **The API is the authority on every number** (#48's rule): clients send lines; totals and
  per-rate tax groups are recomputed here on every write, in ``Decimal``, via ``calc.py``.
- **Snapshots over joins** (#64's rule): a line freezes its tax pct+name at write; a
  document freezes its bill-to block at issue. Editing the picker rows later never rewrites
  what a client was sent.
- **Numbers allocate at issue, under a row lock** (``numbering.py``): drafts are free,
  sequences are per org and optionally per year, and two admins issuing concurrently
  serialize on the settings row instead of colliding.
- **Issued money is immutable**: after ``draft`` the money-bearing fields refuse edits
  (``errors.invoicing.locked``); corrections are a credit note, like a bookkeeper expects.
- **Cross-module touchpoints are bare-table reads/updates through published columns** (§6):
  time entries are selected/stamped by column, never by importing the time module's service.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, func, select, text, tuple_

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.branding import load_brand_logo, load_org_image
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.models import OrgSettings
from app.core.numbering import format_number
from app.core.phone import normalize_phone
from app.core.richtext import sanitize_markdown
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.i18n import translate
from app.modules.invoicing.calc import (
    LineInput,
    Totals,
    compute_totals,
    line_amount,
    round_cents,
)
from app.modules.invoicing.models import (
    DocumentTemplate,
    ExternalRef,
    Invoice,
    InvoiceDomainPeriod,
    InvoiceKind,
    InvoiceLine,
    InvoicePayment,
    InvoiceStatus,
    InvoiceSubscriptionPeriod,
    InvoiceTimeEntry,
    InvoicingSettings,
    LineKind,
    Product,
    Quote,
    QuoteLine,
    QuoteStatus,
    TaxRate,
)
from app.modules.invoicing.render import (
    DocumentBrand,
    render_document_html,
    render_document_pdf,
    resolve_layout,
    validate_custom_source,
)
from app.modules.invoicing.sample import sample_document
from app.modules.invoicing.schemas import (
    DocumentSend,
    InvoiceCreate,
    InvoiceFromTime,
    InvoiceIssue,
    InvoiceUpdate,
    InvoicingSettingsWrite,
    LineWrite,
    PaymentWrite,
    ProductCreate,
    ProductUpdate,
    QuoteCreate,
    QuoteDecision,
    QuoteUpdate,
    TaxRateCreate,
    TaxRateUpdate,
    TemplateCreate,
    TemplateUpdate,
)
from app.modules.invoicing.taxseeds import seeds_for

ENTITY_INVOICE = "invoice"
ENTITY_QUOTE = "quote"

#: Definition fields the activity trail diffs (§16). Totals are included on purpose: a line
#: edit shows up as the money moving, which is the question a trail on an invoice answers.
_AUDITED_INVOICE_FIELDS = (
    "status", "number", "company_id", "contact_id", "issue_date", "due_date", "currency",
    "exchange_rate", "locale", "reference", "template_id", "prices_include_tax",
    "subtotal", "total", "reminders_paused", "delivery_date",
)
_AUDITED_QUOTE_FIELDS = (
    "status", "number", "company_id", "contact_id", "issue_date", "valid_until", "currency",
    "exchange_rate", "locale", "reference", "template_id", "prices_include_tax",
    "subtotal", "total",
)

#: The time module's **"to invoice"** set, as one SQL fragment over a ``time_entries te``.
#:
#: The definition is the time module's (`time/models.py`): ended, billable, approved, not yet
#: invoiced, and worth more than nothing. It was written out four times here in near-identical
#: prose, and they had already drifted — the org-wide report omits ``minutes > 0`` where the
#: per-client list has it. A predicate that decides what a client is charged is not a thing to
#: keep four copies of, so the copies now bind ``:oid`` and add their own extra clauses.
_TO_INVOICE = """
    te.org_id = :oid AND te.ended_at IS NOT NULL
    AND te.billable AND te.approved_at IS NOT NULL AND te.invoiced_at IS NULL
    AND te.minutes > 0
"""

#: How many outstanding entries the Hours picker fetches at once. Over it is reported, never
#: silently cut — the totals beside the list stay exact (see ``_unbilled_totals``).
MAX_UNBILLED_ENTRIES = 500


@dataclass(frozen=True)
class _ClaimSource:
    """One kind of billing period an invoice can claim.

    A renewal and a retainer differ only in which table the id points at and which cron
    consults the claim, so the reconcile is written once and parameterised. ``table`` is a
    bare table name (§6): the claim is validated against the owning module's rows without
    importing them, exactly as ``invoice_time_entries`` validates against ``time_entries``.
    """

    model: type[Any]
    column: str
    table: str


#: Both claim tables, in the order a document's lines are most likely to carry them.
_CLAIM_SOURCES: tuple[_ClaimSource, ...] = (
    _ClaimSource(InvoiceSubscriptionPeriod, "subscription_id", "subscriptions"),
    _ClaimSource(InvoiceDomainPeriod, "domain_id", "domains"),
)

#: Fields an issued (non-draft) document may still edit: rendering and process, never money.
_POST_ISSUE_INVOICE_FIELDS = frozenset(
    # `delivery_date` is a rendering/process fact, not money: correcting the leverdatum on a
    # sent invoice is exactly the kind of edit this set exists to allow.
    {"contact_id", "reference", "intro", "notes", "template_id", "locale", "due_date",
     "reminders_paused", "exchange_rate", "custom", "delivery_date"}
)
_POST_ISSUE_QUOTE_FIELDS = frozenset(
    {"contact_id", "reference", "intro", "notes", "template_id", "locale", "valid_until",
     "exchange_rate", "custom"}
)

INVOICE_SORTABLE = {
    "number": Invoice.number,
    "status": Invoice.status,
    "issue_date": Invoice.issue_date,
    "due_date": Invoice.due_date,
    "total": Invoice.total,
    "created_at": Invoice.created_at,
}
QUOTE_SORTABLE = {
    "number": Quote.number,
    "status": Quote.status,
    "issue_date": Quote.issue_date,
    "valid_until": Quote.valid_until,
    "total": Quote.total,
    "created_at": Quote.created_at,
}

#: The uninvoiced report's grouping expressions (#277) — static SQL fragments keyed by the
#: API's closed vocabulary, never user input. Date buckets format the org-local timestamp
#: (``AT TIME ZONE :tz``) so a bucket is the org's calendar day/week/month/year, not UTC's;
#: every format sorts chronologically as text (``IYYY``/``IW`` are the ISO week fields).
_UNINVOICED_DATE_GROUPS = frozenset({"day", "week", "month", "year"})
_UNINVOICED_GROUP_EXPR = {
    "day": "to_char(te.started_at AT TIME ZONE :tz, 'YYYY-MM-DD')",
    "week": "to_char(te.started_at AT TIME ZONE :tz, 'IYYY-\"W\"IW')",
    "month": "to_char(te.started_at AT TIME ZONE :tz, 'YYYY-MM')",
    "year": "to_char(te.started_at AT TIME ZONE :tz, 'YYYY')",
    "company": "COALESCE(te.company_id::text, '')",
    "project": "COALESCE(te.project_id::text, '')",
    "user": "te.user_id::text",
}
#: Entity groupings: (label expression, its join). Bare-table reads over published columns
#: (§6), each side org-filtered like every join in ``_unbilled_rows``.
_UNINVOICED_GROUP_LABEL = {
    "company": ("c.name", "LEFT JOIN companies c ON c.id = te.company_id AND c.org_id = te.org_id"),
    "project": ("p.name", "LEFT JOIN projects p ON p.id = te.project_id AND p.org_id = te.org_id"),
    "user": ("u.full_name", "LEFT JOIN users u ON u.id = te.user_id"),
}

#: Company columns a document snapshot copies (models.Company, issue #11).
_CUSTOMER_FIELDS = (
    "name", "address_line1", "address_line2", "postal_code", "city", "country",
    "vat_number", "coc_number", "client_number",
)


def street_line(street: str | None, house_number: str | None) -> str | None:
    """The one address line a document prints: street + house number (#241).

    The company stores them apart since the postcode lookup; a snapshot keeps the composed
    line so every issued document — old and new — carries the same ``address_line1`` shape
    and the PDF/UBL renderers never learn about the split. A pre-split row (house number
    still inside ``address_line1``, ``house_number`` NULL) composes to itself.
    """
    if not street:
        return house_number or None
    return f"{street} {house_number}" if house_number else street


def tax_label(label_i18n: dict, locale: str) -> str:
    """A tax rate's display name in a document's locale, falling back sanely."""
    for candidate in (locale, "en", "nl"):
        if label_i18n.get(candidate):
            return label_i18n[candidate]
    return next(iter(label_i18n.values()), "")


async def org_today(ctx: Any) -> date:
    """Today in the org's zone (§8): due dates and overdue are local-calendar concepts."""
    return datetime.now(await org_zoneinfo(ctx.session, ctx.org.id)).date()


# --------------------------------------------------------------------------- #
# Settings
# --------------------------------------------------------------------------- #
class InvoicingSettingsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(InvoicingSettings)

    async def row(self) -> InvoicingSettings:
        """The org's settings row, created lazily with defaults on first touch."""
        existing = await self.ctx.session.scalar(self.repo.scoped_select())
        if existing is not None:
            return existing
        org_settings = await self.ctx.session.scalar(
            self.ctx.repo(OrgSettings).scoped_select()
        )
        # Prefill the seller name from the brand: right for most, editable for the rest.
        details = {"name": org_settings.brand_name} if org_settings else {}
        return await self.repo.create(company_details=details)

    async def save(self, data: InvoicingSettingsWrite) -> InvoicingSettings:
        self.ctx.require("invoicing.settings.manage")
        row = await self.row()
        values = data.model_dump(exclude_unset=True)
        if "company_details" in values and data.company_details is not None:
            details = data.company_details.model_dump(mode="json")
            # Same gate as companies/contacts (#256): only a *changed* phone is validated,
            # so a pre-existing freeform seller phone never blocks an unrelated save.
            if details.get("phone") != (row.company_details or {}).get("phone"):
                details["phone"] = normalize_phone(details.get("phone"))
            values["company_details"] = details
        if values.get("tax_country"):
            values["tax_country"] = values["tax_country"].upper()
        if "default_tax_rate_id" in values and data.default_tax_rate_id is not None:
            await _ensure_tax_rate(self.ctx, data.default_tax_rate_id)
        if "default_template_id" in values and data.default_template_id is not None:
            await _ensure_template(self.ctx, data.default_template_id)
        return await self.repo.update(row, **values)

    async def allocate_number(self, kind: str) -> str:
        """The next document number, race-safe: the settings row is locked for the rest of
        the issuing transaction, so concurrent issues serialize here."""
        row = await self.row()
        locked = await self.ctx.session.scalar(
            select(InvoicingSettings)
            .where(InvoicingSettings.id == row.id, InvoicingSettings.org_id == self.ctx.org.id)
            .with_for_update()
        )
        year = (await org_today(self.ctx)).year
        fmt = locked.invoice_number_format if kind == "invoice" else locked.quote_number_format
        seq_attr = "invoice_next_seq" if kind == "invoice" else "quote_next_seq"
        year_attr = "invoice_seq_year" if kind == "invoice" else "quote_seq_year"

        seq = getattr(locked, seq_attr)
        if locked.number_reset_yearly and getattr(locked, year_attr) not in (None, year):
            seq = 1
        model = Invoice if kind == "invoice" else Quote
        # A manually rewound sequence (settings allow it, for taking over existing books)
        # may point at a number that already exists; walk past collisions, bounded.
        for _ in range(1000):
            number = format_number(fmt, year=year, seq=seq)
            taken = await self.ctx.session.scalar(
                select(model.id).where(model.org_id == self.ctx.org.id, model.number == number)
            )
            if taken is None:
                setattr(locked, seq_attr, seq + 1)
                setattr(locked, year_attr, year)
                await self.ctx.session.flush()
                return number
            seq += 1
        raise AppError("conflict", "errors.invoicing.number_exhausted", status_code=409)


async def _ensure_tax_rate(ctx: RequestContext, tax_rate_id: uuid.UUID) -> TaxRate:
    rate = await ctx.session.scalar(
        select(TaxRate).where(TaxRate.org_id == ctx.org.id, TaxRate.id == tax_rate_id)
    )
    if rate is None:
        raise AppError(
            "validation", "errors.validation", status_code=400,
            fields={"tax_rate_id": "errors.not_found"},
        )
    return rate


async def _load_background(
    ctx: RequestContext, config: dict[str, Any]
) -> tuple[bytes | None, str | None]:
    """The template's own background image, if it has one.

    Only its *own*: when the config has no ``file_id``, the renderer falls back to the org
    logo, which the caller has already loaded. Fetching the same bytes twice for the common
    case would be one storage read per document for nothing.
    """
    raw = config.get("background") or {}
    if not isinstance(raw, dict) or not raw.get("enabled") or not raw.get("file_id"):
        return None, None
    try:
        file_id = uuid.UUID(str(raw["file_id"]))
    except (TypeError, ValueError):
        return None, None
    return await load_org_image(ctx, file_id, what="document background")


async def _ensure_template(ctx: RequestContext, template_id: uuid.UUID) -> None:
    ok = await ctx.session.scalar(
        select(DocumentTemplate.id).where(
            DocumentTemplate.org_id == ctx.org.id, DocumentTemplate.id == template_id
        )
    )
    if ok is None:
        raise AppError(
            "validation", "errors.validation", status_code=400,
            fields={"template_id": "errors.not_found"},
        )


# --------------------------------------------------------------------------- #
# Tax rates
# --------------------------------------------------------------------------- #
class TaxRateService:
    """CRUD for the tenant's tax catalog, seeded per country (``taxseeds.py``).

    The leave-holidays discipline: seeding only fills an **empty** catalog, so nothing a
    tenant renamed, re-rated or deactivated is ever resurrected by a later read.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(TaxRate)

    async def list(self, *, include_inactive: bool = False) -> Sequence[TaxRate]:
        await self._ensure_seeded()
        stmt = self.repo.scoped_select()
        if not include_inactive:
            stmt = stmt.where(TaxRate.active.is_(True))
        stmt = stmt.order_by(TaxRate.position, TaxRate.rate.desc())
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def _ensure_seeded(self) -> None:
        """Lazy seed, only for someone who could have created rates by hand — a read must
        not write on a pure reader's behalf (the subscription-types rule)."""
        if not self.ctx.can("invoicing.settings.manage"):
            return
        if await self.repo.count() > 0:
            return
        country = (await InvoicingSettingsService(self.ctx).row()).tax_country
        for seed in seeds_for(country):
            await self.repo.create(country=country, **seed)

    async def create(self, data: TaxRateCreate) -> TaxRate:
        self.ctx.require("invoicing.settings.manage")
        values = data.model_dump(mode="json")
        if values.get("country"):
            values["country"] = values["country"].upper()
        rate = await self.repo.create(**values)
        if rate.is_default:
            await self._make_sole_default(rate)
        return rate

    async def update(self, tax_rate_id: uuid.UUID, data: TaxRateUpdate) -> TaxRate:
        self.ctx.require("invoicing.settings.manage")
        rate = await self.repo.get_or_404(tax_rate_id)
        values = data.model_dump(mode="json", exclude_unset=True)
        if values.get("country"):
            values["country"] = values["country"].upper()
        rate = await self.repo.update(rate, **values)
        if rate.is_default:
            await self._make_sole_default(rate)
        return rate

    async def delete(self, tax_rate_id: uuid.UUID) -> None:
        """Lines carry snapshots and FK SET NULL, so deleting a rate never rewrites a
        document — deactivating first is still the kinder path and the UI leads with it."""
        self.ctx.require("invoicing.settings.manage")
        rate = await self.repo.get_or_404(tax_rate_id)
        await self.repo.delete(rate)

    async def _make_sole_default(self, rate: TaxRate) -> None:
        others = await self.ctx.session.scalars(
            self.repo.scoped_select().where(TaxRate.id != rate.id, TaxRate.is_default.is_(True))
        )
        for other in others:
            other.is_default = False
        await self.ctx.session.flush()

    async def default_rate(self) -> TaxRate | None:
        return await self.ctx.session.scalar(
            self.repo.scoped_select()
            .where(TaxRate.is_default.is_(True), TaxRate.active.is_(True))
            .limit(1)
        )


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
class ProductService:
    """CRUD for the tenant's default products (owner request) — line presets, org-wide.

    Deleting or re-pricing a product never touches a document: the line editor copies the
    values onto the line, which snapshots them like everything else it holds.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(Product)

    async def list(self, *, include_inactive: bool = False) -> Sequence[Product]:
        stmt = self.repo.scoped_select()
        if not include_inactive:
            stmt = stmt.where(Product.active.is_(True))
        stmt = stmt.order_by(Product.position, func.lower(Product.name))
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def create(self, data: ProductCreate) -> Product:
        self.ctx.require("invoicing.settings.manage")
        values = data.model_dump()
        await self._ensure_tax_rate(values.get("tax_rate_id"))
        return await self.repo.create(**values)

    async def update(self, product_id: uuid.UUID, data: ProductUpdate) -> Product:
        self.ctx.require("invoicing.settings.manage")
        product = await self.repo.get_or_404(product_id)
        values = data.model_dump(exclude_unset=True)
        if "tax_rate_id" in values:
            await self._ensure_tax_rate(values.get("tax_rate_id"))
        return await self.repo.update(product, **values)

    async def delete(self, product_id: uuid.UUID) -> None:
        self.ctx.require("invoicing.settings.manage")
        product = await self.repo.get_or_404(product_id)
        await self.repo.delete(product)

    async def _ensure_tax_rate(self, tax_rate_id: uuid.UUID | None) -> None:
        if tax_rate_id is None:
            return
        ok = await self.ctx.session.scalar(
            self.ctx.repo(TaxRate).scoped_select().where(TaxRate.id == tax_rate_id)
        )
        if ok is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"tax_rate_id": "errors.validation"},
            )


class TemplateService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(DocumentTemplate)

    async def list(self, *, include_inactive: bool = False) -> Sequence[DocumentTemplate]:
        await self._ensure_default_template()
        stmt = self.repo.scoped_select()
        if not include_inactive:
            stmt = stmt.where(DocumentTemplate.active.is_(True))
        stmt = stmt.order_by(DocumentTemplate.position, func.lower(DocumentTemplate.name))
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def _ensure_default_template(self) -> None:
        if not self.ctx.can("invoicing.settings.manage"):
            return
        if await self.repo.count() > 0:
            return
        await self.repo.create(name="Standaard", config={}, is_default=True)

    async def create(self, data: TemplateCreate) -> DocumentTemplate:
        self.ctx.require("invoicing.settings.manage")
        values = data.model_dump(mode="json")
        values["name"] = values["name"].strip()
        values["config"] = self._vet_config(values.get("config") or {}, previous=None)
        template = await self.repo.create(**values)
        if template.is_default:
            await self._make_sole_default(template)
        return template

    async def update(self, template_id: uuid.UUID, data: TemplateUpdate) -> DocumentTemplate:
        self.ctx.require("invoicing.settings.manage")
        template = await self.repo.get_or_404(template_id)
        values = data.model_dump(mode="json", exclude_unset=True)
        if values.get("name"):
            values["name"] = values["name"].strip()
        if "config" in values and values["config"] is not None:
            values["config"] = self._vet_config(values["config"], previous=template.config or {})
        template = await self.repo.update(template, **values)
        if template.is_default:
            await self._make_sole_default(template)
        return template

    def _vet_config(self, config: dict[str, Any], *, previous: dict[str, Any] | None) -> dict:
        """Authorize, validate and normalise a template config before it is stored.

        Three things happen here rather than in the schema, because each needs the caller or
        the row and a Pydantic validator has neither:

        * **Authoring code is its own permission.** ``invoicing.settings.manage`` lets an
          admin arrange blocks and pick colours; writing Jinja that runs on the agency's
          server is a strictly larger act, so ``html``/``css`` need
          ``invoicing.template.author``. Unchanged values pass — otherwise an admin without
          the permission could not rename a template that happens to have custom HTML, and
          would have to delete it to edit it (§17's grandfathering rule, applied to authoring).
        * **A template that cannot render is refused at save.** A Jinja syntax error found
          now is a red field under the editor; found at send time it is an invoice that will
          not go out, discovered by whoever was trying to send it.
        * **The legacy knobs are rewritten from the layout.** ``show_logo`` and ``columns``
          predate layouts and are still what an un-laid-out template renders from; deriving
          them here is what keeps the two from ever disagreeing.
        """
        previous = previous or {}
        for key in ("html", "css"):
            new, old = config.get(key), previous.get(key)
            if (new or "") != (old or "") and (new or "").strip():
                self.ctx.require("invoicing.template.author")
        validate_custom_source(config.get("html"), config.get("css"))

        layout = config.get("layout") or []
        if layout:
            resolved = resolve_layout(layout)
            config["show_logo"] = resolved.enabled("logo")
            config["columns"] = {
                key: resolved.shows("lines", key)
                for key in ("quantity", "unit", "unit_price", "tax")
            }
        return config

    async def delete(self, template_id: uuid.UUID) -> None:
        self.ctx.require("invoicing.settings.manage")
        template = await self.repo.get_or_404(template_id)
        await self.repo.delete(template)

    async def preview(self, config: Any, template_id: uuid.UUID | None = None) -> str:
        """A sample document rendered with an **unsaved** config — the editor's live preview.

        Unsaved is the whole point: the author is judging a change before committing to it. So
        it goes through the same authorization the save does — writing Jinja that runs on the
        server needs ``invoicing.template.author`` whether or not the result is stored — and
        the same render path. It never touches a real document.
        """
        self.ctx.require("invoicing.settings.manage")
        values = config.model_dump(mode="json") if hasattr(config, "model_dump") else dict(config)
        # The saved template is the baseline, so an admin who holds `settings.manage` but not
        # `template.author` can still *see* a custom design they are allowed to open — they
        # just cannot change its code. Absent an id every non-empty body reads as newly
        # written, which is the safe way round.
        previous: dict[str, Any] | None = None
        if template_id is not None:
            stored = await self.ctx.session.scalar(
                self.repo.scoped_select().where(DocumentTemplate.id == template_id)
            )
            previous = (stored.config or {}) if stored is not None else None
        self._vet_config(values, previous=previous)

        settings_row = await InvoicingSettingsService(self.ctx).row()
        org_settings = await self.ctx.session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == self.ctx.org.id)
        )
        currency, locale = await _org_defaults(self.ctx)
        doc, lines, groups = sample_document(locale, currency, await org_today(self.ctx))
        logo, logo_type = await load_brand_logo(self.ctx, org_settings)
        background, background_type = await _load_background(self.ctx, values)
        return render_document_html(
            kind="invoice",
            doc=doc,
            lines=lines,
            seller=settings_row.company_details or {},
            config=values,
            brand=DocumentBrand(
                name=(org_settings.brand_name if org_settings else None) or self.ctx.org.name,
                primary_color=org_settings.primary_color if org_settings else None,
                logo=logo,
                logo_content_type=logo_type,
                background=background,
                background_content_type=background_type,
            ),
            tax_groups=groups,
        )

    async def _make_sole_default(self, template: DocumentTemplate) -> None:
        others = await self.ctx.session.scalars(
            self.repo.scoped_select().where(
                DocumentTemplate.id != template.id, DocumentTemplate.is_default.is_(True)
            )
        )
        for other in others:
            other.is_default = False
        await self.ctx.session.flush()


# --------------------------------------------------------------------------- #
# Shared document machinery
# --------------------------------------------------------------------------- #
async def _company_row(ctx: Any, company_id: uuid.UUID) -> Any:
    row = (
        await ctx.session.execute(
            text(
                "SELECT id, name, invoice_email, vat_number, coc_number, address_line1,"
                " house_number, address_line2, postal_code, city, country, client_number"
                " FROM companies WHERE id = :cid AND org_id = :oid"
            ),
            {"cid": company_id, "oid": ctx.org.id},
        )
    ).mappings().first()
    if row is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return row


async def _contact_party(ctx: Any, contact_id: uuid.UUID) -> tuple[str | None, str | None]:
    """``(email, display name)`` of a contact, tenant-scoped.

    The name is what a document prints as *t.a.v.* — snapshotted onto the invoice like every
    other addressee field (#64's rule), so a contact who later leaves the client does not
    rewrite the invoice that was sent to them.
    """
    row = (
        await ctx.session.execute(
            text(
                "SELECT id, email, first_name, last_name FROM contacts"
                " WHERE id = :cid AND org_id = :oid"
            ),
            {"cid": contact_id, "oid": ctx.org.id},
        )
    ).mappings().first()
    if row is None:
        raise AppError(
            "validation", "errors.validation", status_code=400,
            fields={"contact_id": "errors.not_found"},
        )
    name = " ".join(part for part in (row["first_name"], row["last_name"]) if part)
    return row["email"], (name or None)


def _customer_snapshot(
    company: Any, *, email: str | None, attn: str | None = None
) -> dict[str, Any]:
    data = {field: company[field] for field in _CUSTOMER_FIELDS}
    data["address_line1"] = street_line(company["address_line1"], company["house_number"])
    data["email"] = email or company["invoice_email"]
    # The contact the document is addressed to. Frozen here with the rest of the addressee,
    # so a template that prints it prints who it was actually sent to.
    data["attn"] = attn
    return data


async def _snapshot_lines(
    ctx: Any,
    lines: list[LineWrite],
    *,
    locale: str,
    default_tax_rate_id: uuid.UUID | None,
    provenance: bool = False,
) -> list[dict[str, Any]]:
    """Line rows ready to insert: tax resolved (scoped!) and frozen, amounts computed.

    ``provenance`` carries *what the line bills* (entries, an agreement's period, a domain's
    renewal) onto the row. Invoices only — ``quote_lines`` has no such columns, because a
    quote bills nothing and claims nothing.
    """
    wanted = {
        line.tax_rate_id or default_tax_rate_id
        for line in lines
        if (line.tax_rate_id or default_tax_rate_id) is not None
    }
    rates: dict[uuid.UUID, TaxRate] = {}
    if wanted:
        found = (
            await ctx.session.execute(
                select(TaxRate).where(TaxRate.org_id == ctx.org.id, TaxRate.id.in_(wanted))
            )
        ).scalars()
        rates = {rate.id: rate for rate in found}

    rows: list[dict[str, Any]] = []
    for index, line in enumerate(lines):
        rate_id = line.tax_rate_id or default_tax_rate_id
        if line.tax_rate_id is not None and line.tax_rate_id not in rates:
            # An id that isn't this tenant's resolves to nothing — refuse, never guess.
            raise AppError(
                "validation", "errors.validation", status_code=400,
                fields={"lines": "errors.not_found"},
            )
        rate = rates.get(rate_id) if rate_id else None
        row = {
            "position": index,
            "line_kind": str(line.line_kind),
            "description": line.description.strip(),
            "quantity": line.quantity,
            "unit": line.unit,
            "unit_price": line.unit_price,
            "tax_rate_id": rate.id if rate else None,
            "tax_rate_pct": rate.rate if rate else Decimal(0),
            "tax_name": tax_label(rate.label_i18n, locale) if rate else "",
            "tax_category": rate.category if rate else "standard",
            "amount": line_amount(line.quantity, line.unit_price),
        }
        if provenance:
            # What this line bills, stored **on the line**. The document's lines are replaced
            # wholesale on every save, so provenance kept only in the claim tables could not
            # survive the round trip: the editor re-posted lines that had forgotten their
            # claims, the reconcile dutifully released them, and the cron billed the period
            # again. Quotes claim nothing, hence the flag rather than an unconditional write.
            row |= {
                "time_entry_ids": [str(eid) for eid in line.time_entry_ids],
                "subscription_id": line.subscription_id,
                "domain_id": line.domain_id,
                "period_start": line.period_start,
                "period_end": line.period_end,
            }
        rows.append(row)
    return rows


def _totals_from_rows(rows: Sequence[Any], *, prices_include_tax: bool) -> Totals:
    return compute_totals(
        [
            LineInput(
                quantity=Decimal(row["quantity"]) if isinstance(row, dict) else row.quantity,
                unit_price=(
                    Decimal(row["unit_price"]) if isinstance(row, dict) else row.unit_price
                ),
                tax_rate_pct=(
                    Decimal(row["tax_rate_pct"]) if isinstance(row, dict) else row.tax_rate_pct
                ),
                tax_category=(
                    row["tax_category"] if isinstance(row, dict) else row.tax_category
                ),
                tax_name=row["tax_name"] if isinstance(row, dict) else row.tax_name,
            )
            for row in rows
        ],
        prices_include_tax=prices_include_tax,
    )


async def _org_defaults(ctx: Any) -> tuple[str, str]:
    """(currency, locale) the org works in — the document's defaults (#124, §8)."""
    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == ctx.org.id)
    )
    if org_settings is None:
        return "EUR", "nl"
    return org_settings.currency, org_settings.default_locale


class _DocumentService:
    """What ``InvoiceService`` and ``QuoteService`` share. ``model``/``line_model``/
    ``line_fk`` parametrize the tables; everything money-shaped is identical by design."""

    model: type
    line_model: type
    line_fk: str
    entity_type: str
    audited_fields: tuple[str, ...]
    post_issue_fields: frozenset[str]

    class _PortalDocumentRepository(TenantScopedRepository):
        """The document repo an external (client) login gets (#266) — the contacts pattern.

        It follows ``ctx.is_portal``, which since #274 means *any* client-role login and not
        only a contact-linked one, and it defers to the model's own
        ``__portal_horizon_clause__``: the company match, **and** the agency's drafts left
        out.

        It overrides ``horizon_condition``, not ``_scoped``: the predicate is then the *one*
        answer every path takes — ``get_or_404`` (so the detail and the ``/pdf``,
        ``/preview`` and ``/ubl`` downloads that load through it), ``scoped_select`` (the
        list and ``for_company``, which is what the company-detail panel a client can already
        open renders), and ``scoped_count_select`` (the list's total, so it counts exactly
        the rows the list could return). Overriding ``_scoped`` left the others reading the
        looser staff rule — that was the #285 bug, and the reason it is stated once.
        """

        def horizon_condition(self):  # noqa: ANN202 — mirrors the base signature
            clause = getattr(self.model, "__portal_horizon_clause__", None)
            if clause is None:  # a document type that has not declared one — stay strict
                return super().horizon_condition()
            return clause(self.company_scope)

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = (
            self._PortalDocumentRepository(
                ctx.session, ctx.org.id, self.model, company_scope=ctx.company_scope
            )
            if ctx.is_portal
            else ctx.repo(self.model)
        )
        # Deliberately the plain repo: a line carries no ``company_id``, and is only ever
        # reached through a parent id this repository has already filtered.
        self.lines = ctx.repo(self.line_model)
        self.settings = InvoicingSettingsService(ctx)
        self.custom_fields = CustomFieldsService(ctx)

    @property
    def issued_only(self) -> bool:
        """Does this caller read *issued* documents only — never the agency's drafts (#266)?

        True for an **external login**, and that is deliberately the axis rather than the
        permission's scope: a draft has no number, no legal standing and may still change, so
        whether it is yours to see is a question about *who is asking*, not about how broad
        their grant is. Restricted staff — a membership scoped to one company group (#191) —
        still see that client's drafts, because drafting the invoice is their job.

        The reads do not consult this; they inherit it from ``_PortalDocumentRepository``
        above, which is the point. It is here for the two answers that cannot ride a
        repository at all: the summary's hand-written aggregate, and the ``status`` filter,
        which must not offer a value the caller can never match.
        """
        return self.ctx.is_portal

    async def _render_inputs(self, doc: Any, kind: str) -> dict[str, Any]:
        """Everything the renderer needs, resolved here rather than there.

        White-label identity — logo bytes, brand colour, brand name — is loaded at this
        boundary because ``render/`` may not own a default identity of its own (Golden Rule
        4). The template resolves as the document's own and nothing implied when it has none,
        so the paper a client receives and the preview on screen are the same design; they
        are in fact the same HTML.
        """
        settings_row = await self.settings.row()
        config: dict[str, Any] = {}
        if doc.template_id is not None:
            template = await self.ctx.session.scalar(
                self.ctx.repo(DocumentTemplate)
                .scoped_select()
                .where(DocumentTemplate.id == doc.template_id)
            )
            if template is not None:
                config = template.config or {}
        org_settings = await self.ctx.session.scalar(
            select(OrgSettings).where(OrgSettings.org_id == self.ctx.org.id)
        )
        logo, logo_type = await load_brand_logo(self.ctx, org_settings)
        background, background_type = await _load_background(self.ctx, config)
        return {
            "kind": kind,
            "doc": doc,
            "lines": list(doc.lines),
            "seller": settings_row.company_details or {},
            "config": config,
            "brand": DocumentBrand(
                name=(org_settings.brand_name if org_settings else None) or self.ctx.org.name,
                primary_color=org_settings.primary_color if org_settings else None,
                logo=logo,
                logo_content_type=logo_type,
                background=background,
                background_content_type=background_type,
            ),
            "tax_groups": _totals_from_rows(
                list(doc.lines), prices_include_tax=doc.prices_include_tax
            ).groups,
        }

    async def document_html(self, doc: Any, kind: str) -> str:
        """The document as a standalone HTML page — what the preview shows.

        The *same* artefact :meth:`document_pdf` prints, which is the point: there is no
        second renderer to keep in step. ``doc`` must have its lines attached (``get()`` does).
        """
        return render_document_html(**await self._render_inputs(doc, kind))

    async def document_pdf(self, doc: Any, kind: str) -> tuple[bytes, str]:
        """The same document, printed. Returns the bytes and a filename.

        Printing is CPU-bound and blocking, so it runs in a thread — the rule the storage
        routes follow (#190). A long invoice would otherwise stall every other request on the
        worker for the duration of the layout.
        """
        inputs = await self._render_inputs(doc, kind)
        content = await asyncio.to_thread(lambda: render_document_pdf(**inputs))
        prefix = "offerte" if kind == "quote" else "factuur"
        return content, f"{doc.number or f'{prefix}-{doc.id}'}.pdf"

    async def _default_tax_rate_id(self, settings_row: InvoicingSettings) -> uuid.UUID | None:
        """The rate a line without one gets: the configured default, else the catalog's
        ``is_default`` row (which is what the seeder marks)."""
        if settings_row.default_tax_rate_id is not None:
            return settings_row.default_tax_rate_id
        rate = await TaxRateService(self.ctx).default_rate()
        return rate.id if rate else None

    async def _replace_lines(self, doc: Any, line_rows: list[dict[str, Any]]) -> Totals:
        for row in await self.ctx.session.scalars(
            self.lines.scoped_select().where(
                getattr(self.line_model, self.line_fk) == doc.id
            )
        ):
            await self.lines.delete(row)
        for row in line_rows:
            await self.lines.create(**{self.line_fk: doc.id}, **row)
        totals = _totals_from_rows(line_rows, prices_include_tax=doc.prices_include_tax)
        await self.repo.update(
            doc, subtotal=totals.subtotal, tax_total=totals.tax_total, total=totals.total
        )
        return totals

    async def _doc_lines(self, doc_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[Any]]:
        rows = (
            await self.ctx.session.execute(
                self.lines.scoped_select()
                .where(getattr(self.line_model, self.line_fk).in_(doc_ids))
                .order_by(self.line_model.position)
            )
        ).scalars()
        by_doc: dict[uuid.UUID, list[Any]] = {}
        for row in rows:
            by_doc.setdefault(getattr(row, self.line_fk), []).append(row)
        return by_doc

    async def _company_names(self, docs: Sequence[Any]) -> dict[uuid.UUID, str]:
        if not docs:
            return {}
        rows = (
            await self.ctx.session.execute(
                text("SELECT id, name FROM companies WHERE org_id = :oid AND id IN :ids")
                .bindparams(bindparam("ids", expanding=True)),
                {"oid": self.ctx.org.id, "ids": list({d.company_id for d in docs})},
            )
        ).all()
        return {row[0]: row[1] for row in rows}


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #
class InvoiceService(_DocumentService):
    model = Invoice
    line_model = InvoiceLine
    line_fk = "invoice_id"
    entity_type = ENTITY_INVOICE
    audited_fields = _AUDITED_INVOICE_FIELDS
    post_issue_fields = _POST_ISSUE_INVOICE_FIELDS

    # --- reads --------------------------------------------------------------- #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        company_id: uuid.UUID | None = None,
        kind: str | None = None,
        overdue: bool = False,
        q: str | None = None,
        sort: str | None = None,
        lines: bool = True,
    ) -> tuple[Sequence[Invoice], int]:
        conditions = []
        if status:
            # No draft special-case: for an external login the repository's clause already
            # says ``status != 'draft'``, so ``?status=draft`` ANDs to an empty page — which
            # is the honest answer to "show me the drafts", not a filter to second-guess.
            conditions.append(Invoice.status == status)
        if company_id is not None:
            conditions.append(Invoice.company_id == company_id)
        if kind:
            conditions.append(Invoice.kind == kind)
        if overdue:
            today = await org_today(self.ctx)
            conditions.append(Invoice.status == InvoiceStatus.OPEN.value)
            conditions.append(Invoice.due_date.is_not(None))
            conditions.append(Invoice.due_date < today)
        if q:
            needle = f"%{q.strip()}%"
            conditions.append(
                Invoice.number.ilike(needle) | Invoice.reference.ilike(needle)
            )
        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, INVOICE_SORTABLE, default=Invoice.created_at.desc())
        items = list(
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
        total = int(
            await self.ctx.session.scalar(
                # ``scoped_count_select`` carries the company horizon; the hand-built
                # ``select(count())`` it replaces counted the org's invoices above a restricted
                # membership's filtered rows (#285) — the count leak #252 fixed elsewhere.
                self.repo.scoped_count_select().where(*conditions)
            )
            or 0
        )
        await self._attach(items, lines=lines)
        return items, total

    async def get(self, invoice_id: uuid.UUID) -> Invoice:
        # A draft is not merely forbidden to an external login, it does not exist: the portal
        # repository's clause excludes it, so this answers 404 like any out-of-horizon row
        # (#266, §15's 404-not-403 rule — a 403 would confirm the agency is drafting
        # something for them, which is the fact being withheld).
        invoice = await self.repo.get_or_404(invoice_id)
        await self._attach([invoice], payments=True)
        return invoice

    async def for_company(self, company_id: uuid.UUID, *, limit: int = 8) -> Sequence[Invoice]:
        stmt = (
            self.repo.scoped_select()
            .where(Invoice.company_id == company_id)
            .order_by(Invoice.created_at.desc())
            .limit(limit)
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # The company panel lists number/date/status/total, never a line (#290).
        await self._attach(items, lines=False)
        return items

    async def _attach(
        self, invoices: Sequence[Invoice], *, payments: bool = False, lines: bool = True
    ) -> None:
        """Batch-resolve names/lines/groups/derived flags — a list never N+1s.

        ``lines=False`` is the list's opt-out (#290): the index shows number, client, date,
        status and total, and never a line. Loading every line of every invoice on the page to
        derive tax groups nobody renders is the heaviest thing that response does. ``total``
        and ``paid_total`` are real columns, so ``outstanding``/``overdue`` still answer
        correctly without them.
        """
        if not invoices:
            return
        ids = [i.id for i in invoices]
        names = await self._company_names(invoices)
        lines_by_doc = await self._doc_lines(ids) if lines else {}
        today = await org_today(self.ctx)
        payment_rows: dict[uuid.UUID, list[InvoicePayment]] = {}
        if payments:
            rows = (
                await self.ctx.session.execute(
                    self.ctx.repo(InvoicePayment)
                    .scoped_select()
                    .where(InvoicePayment.invoice_id.in_(ids))
                    .order_by(InvoicePayment.paid_on, InvoicePayment.created_at)
                )
            ).scalars()
            for row in rows:
                payment_rows.setdefault(row.invoice_id, []).append(row)
        for invoice in invoices:
            rows = lines_by_doc.get(invoice.id, [])
            invoice.company_name = names.get(invoice.company_id, "")  # type: ignore[attr-defined]
            invoice.lines = rows  # type: ignore[attr-defined]
            invoice.tax_groups = (  # type: ignore[attr-defined]
                [
                    {
                        "rate_pct": g.rate_pct, "category": g.category, "name": g.name,
                        "base": g.base, "tax": g.tax,
                    }
                    for g in _totals_from_rows(
                        rows, prices_include_tax=invoice.prices_include_tax
                    ).groups
                ]
                if lines
                else []
            )
            invoice.outstanding = invoice.total - invoice.paid_total  # type: ignore[attr-defined]
            invoice.overdue = (  # type: ignore[attr-defined]
                invoice.status == InvoiceStatus.OPEN.value
                and invoice.due_date is not None
                and invoice.due_date < today
            )
            if payments:
                invoice.payments = payment_rows.get(invoice.id, [])  # type: ignore[attr-defined]

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: InvoiceCreate) -> Invoice:
        self.ctx.require("invoicing.invoice.write")
        company = await _company_row(self.ctx, data.company_id)
        contact_email, contact_name = (
            await _contact_party(self.ctx, data.contact_id)
            if data.contact_id
            else (None, None)
        )
        if data.template_id is not None:
            await _ensure_template(self.ctx, data.template_id)
        settings_row = await self.settings.row()
        currency, locale = await _org_defaults(self.ctx)
        doc_locale = data.locale or locale
        include_tax = (
            data.prices_include_tax
            if data.prices_include_tax is not None
            else settings_row.prices_include_tax
        )
        custom = await self.custom_fields.validate(self.entity_type, data.custom or {})
        line_rows = await _snapshot_lines(
            self.ctx, data.lines, locale=doc_locale,
            default_tax_rate_id=await self._default_tax_rate_id(settings_row),
            provenance=True,
        )
        invoice = await self.repo.create(
            company_id=data.company_id,
            contact_id=data.contact_id,
            kind=data.kind.value,
            customer=_customer_snapshot(company, email=contact_email, attn=contact_name),
            currency=(data.currency or currency).upper(),
            exchange_rate=data.exchange_rate,
            locale=doc_locale,
            reference=data.reference,
            intro=data.intro,
            # Markdown source (issue #66/#228): raw HTML is stripped on write.
            notes=sanitize_markdown(data.notes),
            template_id=data.template_id or settings_row.default_template_id,
            issue_date=data.issue_date,
            due_date=data.due_date,
            delivery_date=data.delivery_date,
            prices_include_tax=include_tax,
            custom=custom,
        )
        await self._replace_lines(invoice, line_rows)
        await ActivityService(self.ctx).record_created(self.entity_type, invoice.id)
        # Lines picked from what is still outstanding carry what they bill: bill exactly
        # those, stamping the entries invoiced and claiming the periods. Invalid, foreign or
        # already-billed ids are skipped (``_link_time_entries``); ``from_time``'s own lines
        # carry none and link separately.
        await self._reconcile_time_entries(invoice, data.lines)
        await self._claim_periods(invoice, data.lines)
        await self._attach([invoice], payments=True)
        return invoice

    async def update(self, invoice_id: uuid.UUID, data: InvoiceUpdate) -> Invoice:
        self.ctx.require("invoicing.invoice.write")
        invoice = await self.repo.get_or_404(invoice_id)
        before = snapshot(invoice, self.audited_fields)
        sent = data.model_dump(exclude_unset=True)
        if invoice.status != InvoiceStatus.DRAFT.value:
            # Issued money is immutable — corrections are a credit note (#207).
            locked = set(sent) - self.post_issue_fields
            if locked:
                raise AppError("conflict", "errors.invoicing.locked", status_code=409)

        values: dict[str, Any] = {}
        for field in (
            "reference", "intro", "notes", "issue_date", "due_date", "delivery_date",
            "exchange_rate", "reminders_paused",
        ):
            if field in sent:
                values[field] = sent[field]
        if "notes" in values:
            values["notes"] = sanitize_markdown(values["notes"])
        if "locale" in sent and data.locale is not None:
            values["locale"] = data.locale
        if "currency" in sent and data.currency is not None:
            values["currency"] = data.currency
        if "prices_include_tax" in sent and data.prices_include_tax is not None:
            values["prices_include_tax"] = data.prices_include_tax
        if "contact_id" in sent:
            if data.contact_id is not None:
                email, name = await _contact_party(self.ctx, data.contact_id)
                customer = dict(invoice.customer)
                customer["email"] = email or customer.get("email")
                customer["attn"] = name
                values["customer"] = customer
            values["contact_id"] = data.contact_id
        if "template_id" in sent:
            if data.template_id is not None:
                await _ensure_template(self.ctx, data.template_id)
            values["template_id"] = data.template_id
        if "custom" in sent:
            values["custom"] = await self.custom_fields.validate(
                self.entity_type, data.custom or {}
            )
        invoice = await self.repo.update(invoice, **values)

        if data.lines is not None:
            settings_row = await self.settings.row()
            line_rows = await _snapshot_lines(
                self.ctx, data.lines, locale=invoice.locale,
                default_tax_rate_id=await self._default_tax_rate_id(settings_row),
                provenance=True,
            )
            await self._replace_lines(invoice, line_rows)
            # Both rebuilt from the lines that survive the edit: dropping a subscription line
            # hands its period back to the cycle cron, and dropping an hours line un-bills
            # exactly its entries. The hours half was missing until the lines could carry
            # what they bill — an edited draft stamped entries nobody could ever un-stamp.
            await self._reconcile_time_entries(invoice, data.lines)
            await self._claim_periods(invoice, data.lines)

        await ActivityService(self.ctx).record_update(
            self.entity_type, invoice.id, before, snapshot(invoice, self.audited_fields)
        )
        await self._attach([invoice], payments=True)
        return invoice

    async def delete(self, invoice_id: uuid.UUID) -> None:
        """Drafts only: an issued invoice is a numbered legal document — cancel it instead,
        so the number and the trail survive."""
        self.ctx.require("invoicing.invoice.delete")
        invoice = await self.repo.get_or_404(invoice_id)
        if invoice.status != InvoiceStatus.DRAFT.value:
            raise AppError("conflict", "errors.invoicing.not_draft", status_code=409)
        await self._release_time_entries(invoice.id)
        await self._release_subscription_periods(invoice.id)
        await self._revert_quote(invoice)
        await self.repo.delete(invoice)

    async def issue(self, invoice_id: uuid.UUID, data: InvoiceIssue) -> Invoice:
        self.ctx.require("invoicing.invoice.write")
        invoice = await self.repo.get_or_404(invoice_id)
        if invoice.status != InvoiceStatus.DRAFT.value:
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        line_count = await self.lines.count(**{self.line_fk: invoice.id})
        if line_count == 0:
            raise AppError("validation", "errors.invoicing.no_lines", status_code=400)
        settings_row = await self.settings.row()
        if not (settings_row.company_details or {}).get("name"):
            raise AppError(
                "validation", "errors.invoicing.seller_incomplete", status_code=400
            )
        today = await org_today(self.ctx)
        issue_date = data.issue_date or invoice.issue_date or today
        due_date = data.due_date or invoice.due_date or (
            issue_date + timedelta(days=settings_row.default_due_days)
        )
        # Freeze the bill-to at the moment the document becomes real.
        company = await _company_row(self.ctx, invoice.company_id)
        snapshot = invoice.customer or {}
        email, attn = snapshot.get("email"), snapshot.get("attn")
        number = await self.settings.allocate_number("invoice")
        invoice = await self.repo.update(
            invoice,
            number=number,
            status=InvoiceStatus.OPEN.value,
            issue_date=issue_date,
            due_date=due_date,
            customer=_customer_snapshot(company, email=email, attn=attn),
        )
        await ActivityService(self.ctx).record(
            self.entity_type, invoice.id, "issued", {"number": number}
        )
        await emit(
            "invoice.issued",
            self.ctx,
            {
                "invoice_id": invoice.id,
                "company_id": invoice.company_id,
                "number": number,
                "total": str(invoice.total),
                "currency": invoice.currency,
            },
        )
        await self._attach([invoice], payments=True)
        return invoice

    async def cancel(self, invoice_id: uuid.UUID) -> Invoice:
        self.ctx.require("invoicing.invoice.write")
        invoice = await self.repo.get_or_404(invoice_id)
        if invoice.status != InvoiceStatus.OPEN.value:
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        if invoice.paid_total != 0:
            raise AppError("conflict", "errors.invoicing.has_payments", status_code=409)
        await self._release_time_entries(invoice.id)
        # A cancelled invoice bills nothing, so its periods go back to the cycle cron —
        # otherwise cancelling would silently retire an agreement's month for good.
        await self._release_subscription_periods(invoice.id)
        invoice = await self.repo.update(
            invoice, status=InvoiceStatus.CANCELLED.value, cancelled_at=datetime.now(UTC)
        )
        await ActivityService(self.ctx).record(self.entity_type, invoice.id, "cancelled")
        await self._attach([invoice], payments=True)
        return invoice

    async def credit(self, invoice_id: uuid.UUID) -> Invoice:
        """A draft credit note mirroring the invoice with negated prices — the bookkeeping
        way to correct an issued document (#207: issued money is immutable)."""
        self.ctx.require("invoicing.invoice.write")
        source = await self.repo.get_or_404(invoice_id)
        if source.status not in (InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value):
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        source_lines = (await self._doc_lines([source.id])).get(source.id, [])
        credit = await self.repo.create(
            company_id=source.company_id,
            contact_id=source.contact_id,
            kind=InvoiceKind.CREDIT_NOTE.value,
            credit_for_id=source.id,
            customer=dict(source.customer),
            currency=source.currency,
            exchange_rate=source.exchange_rate,
            locale=source.locale,
            reference=source.number,
            template_id=source.template_id,
            prices_include_tax=source.prices_include_tax,
        )
        line_rows = [
            {
                "position": row.position,
                # A credit note mirrors the document it corrects, section headings included.
                "line_kind": row.line_kind,
                "description": row.description,
                "quantity": row.quantity,
                "unit": row.unit,
                "unit_price": -row.unit_price,
                "tax_rate_id": row.tax_rate_id,
                "tax_rate_pct": row.tax_rate_pct,
                "tax_name": row.tax_name,
                "tax_category": row.tax_category,
                "amount": line_amount(row.quantity, -row.unit_price),
            }
            for row in source_lines
        ]
        await self._replace_lines(credit, line_rows)
        await ActivityService(self.ctx).record_created(self.entity_type, credit.id)
        await ActivityService(self.ctx).record(
            self.entity_type, source.id, "credited", {"credit_id": str(credit.id)}
        )
        await self._attach([credit], payments=True)
        return credit

    async def send(self, invoice_id: uuid.UUID, data: DocumentSend) -> Invoice:
        """Mail the invoice summary to the customer (or record an out-of-band send)."""
        self.ctx.require("invoicing.invoice.send")
        invoice = await self.repo.get_or_404(invoice_id)
        if invoice.status not in (InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value):
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        to = data.to or (invoice.customer or {}).get("email")
        if data.email:
            if not to:
                raise AppError(
                    "validation", "errors.invoicing.no_recipient", status_code=400
                )
            from app.core.email.branding import load_brand
            from app.core.email.senders import EmailAttachment
            from app.modules.invoicing import emails

            brand = await load_brand(self.ctx.session, self.ctx.org)
            message = emails.compose_invoice_email(
                invoice, brand.brand_name, data.message
            )
            message.to = to
            # The mail carries the document (owner feedback): a text summary is not an
            # invoice. Lines ride along for the render.
            await self._attach([invoice], payments=True)
            content, filename = await self.document_pdf(invoice, "invoice")
            message.attachments.append(
                EmailAttachment(filename=filename, content=content, mimetype="application/pdf")
            )
            await emails.deliver(self.ctx, message, brand=brand)
        invoice = await self.repo.update(invoice, sent_at=datetime.now(UTC))
        await ActivityService(self.ctx).record(
            self.entity_type, invoice.id, "sent",
            {"to": to} if data.email else {"external": True},
        )
        await self._attach([invoice], payments=True)
        return invoice

    async def remind(self, invoice_id: uuid.UUID) -> Invoice:
        """A reminder on demand — same mail the cron sends, counted the same way."""
        self.ctx.require("invoicing.invoice.send")
        invoice = await self.repo.get_or_404(invoice_id)
        if invoice.status != InvoiceStatus.OPEN.value:
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        to = (invoice.customer or {}).get("email")
        if not to:
            raise AppError("validation", "errors.invoicing.no_recipient", status_code=400)
        today = await org_today(self.ctx)
        days = (today - invoice.due_date).days if invoice.due_date else 0
        from app.core.email.branding import load_brand
        from app.modules.invoicing import emails

        brand = await load_brand(self.ctx.session, self.ctx.org)
        message = emails.compose_reminder_email(invoice, brand.brand_name, max(days, 0))
        message.to = to
        await emails.deliver(self.ctx, message, brand=brand)
        invoice = await self.repo.update(
            invoice,
            reminder_count=invoice.reminder_count + 1,
            last_reminder_at=datetime.now(UTC),
        )
        await ActivityService(self.ctx).record(
            self.entity_type, invoice.id, "reminder_sent", {"to": to, "manual": True}
        )
        await self._attach([invoice], payments=True)
        return invoice

    # --- payments -------------------------------------------------------------- #
    async def add_payment(self, invoice_id: uuid.UUID, data: PaymentWrite) -> Invoice:
        self.ctx.require("invoicing.payment.write")
        invoice = await self.repo.get_or_404(invoice_id)
        if invoice.status not in (InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value):
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        await self.ctx.repo(InvoicePayment).create(
            invoice_id=invoice.id,
            paid_on=data.paid_on,
            amount=data.amount,
            method=data.method,
            note=data.note,
        )
        await ActivityService(self.ctx).record(
            self.entity_type, invoice.id, "payment_registered",
            {"amount": float(data.amount), "method": data.method},
        )
        return await self._settle(invoice)

    async def delete_payment(self, invoice_id: uuid.UUID, payment_id: uuid.UUID) -> Invoice:
        self.ctx.require("invoicing.payment.write")
        invoice = await self.repo.get_or_404(invoice_id)
        payments = self.ctx.repo(InvoicePayment)
        payment = await payments.get_or_404(payment_id)
        if payment.invoice_id != invoice.id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        await ActivityService(self.ctx).record(
            self.entity_type, invoice.id, "payment_deleted", {"amount": float(payment.amount)}
        )
        await payments.delete(payment)
        return await self._settle(invoice)

    async def _settle(self, invoice: Invoice) -> Invoice:
        """Recompute ``paid_total`` from the payments and flip status accordingly — the sum
        is the truth, a stored counter is only its cache."""
        paid = await self.ctx.session.scalar(
            select(func.coalesce(func.sum(InvoicePayment.amount), 0)).where(
                InvoicePayment.org_id == self.ctx.org.id,
                InvoicePayment.invoice_id == invoice.id,
            )
        )
        paid_total = Decimal(paid or 0)
        was_paid = invoice.status == InvoiceStatus.PAID.value
        fully_paid = invoice.total > 0 and paid_total >= invoice.total
        values: dict[str, Any] = {"paid_total": paid_total}
        if fully_paid and not was_paid:
            values["status"] = InvoiceStatus.PAID.value
            values["paid_at"] = datetime.now(UTC)
        elif not fully_paid and was_paid:
            values["status"] = InvoiceStatus.OPEN.value
            values["paid_at"] = None
        invoice = await self.repo.update(invoice, **values)
        if fully_paid and not was_paid:
            await emit(
                "invoice.paid",
                self.ctx,
                {
                    "invoice_id": invoice.id,
                    "company_id": invoice.company_id,
                    "number": invoice.number,
                    "total": str(invoice.total),
                    "currency": invoice.currency,
                },
            )
        await self._attach([invoice], payments=True)
        return invoice

    # --- time-tracking bridge (issue #207: deeply connected) ------------------- #
    async def unbilled(
        self,
        company_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None = None,
        until: date | None = None,
        limit: int = MAX_UNBILLED_ENTRIES,
    ) -> dict[str, Any]:
        """Approved + billable + not-yet-invoiced entries for a company — what the Hours
        picker chooses from.

        Capped, and the cap is **reported**: a client with eight hundred outstanding entries
        would otherwise push eight hundred rows into a dialog. The count and the money stay
        exact whatever the cap — they come from a `COUNT`/`SUM` over the whole set, not from
        the rows returned — so "12 uren nog te factureren" is never a number the truncation
        quietly shrank.
        """
        self.ctx.require("invoicing.invoice.write")
        await _company_row(self.ctx, company_id)
        rows = await self._unbilled_rows(
            company_id, project_id=project_id, until=until, limit=limit + 1
        )
        truncated = len(rows) > limit
        rows = rows[:limit]
        settings_row = await self.settings.row()
        # Per-entry rate = the logger's effective rate (#226); the invoicing default is the
        # last resort for orgs that haven't configured employee rates yet.
        default_rate = settings_row.default_hourly_rate or Decimal(0)
        totals = await self._unbilled_totals(
            company_id, project_id=project_id, until=until, fallback_rate=default_rate
        )
        return {
            "entries": [
                {
                    "id": row["id"],
                    "started_at": row["started_at"],
                    "minutes": row["minutes"],
                    "description": row["description"],
                    "project_id": row["project_id"],
                    "project_name": row["project_name"] or "",
                    "user_name": row["user_name"] or "",
                    "rate": (
                        Decimal(row["employee_rate"])
                        if row["employee_rate"] is not None
                        else default_rate
                    ),
                }
                for row in rows
            ],
            "total_minutes": totals["minutes"],
            "total_count": totals["count"],
            "total_amount": totals["amount"],
            "hourly_rate": settings_row.default_hourly_rate,
            "truncated": truncated,
        }

    async def _unbilled_totals(
        self,
        company_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None,
        until: date | None,
        fallback_rate: Decimal,
    ) -> dict[str, Any]:
        """Count, minutes and money over the **whole** outstanding set, in one aggregate.

        Separate from the row fetch so the cap can never reach the totals — the two-query
        shape ``uninvoiced_report`` already uses, and the reason a truncated list can still
        say honestly how much is behind it. The rate chain is folded into the SQL so the
        aggregate prices exactly as the entry list does (#226).
        """
        clauses, params = await self._unbilled_clauses(
            company_id, project_id=project_id, until=until
        )
        params["fallback_rate"] = fallback_rate
        row = (
            await self.ctx.session.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS cnt,
                           COALESCE(SUM(te.minutes), 0) AS minutes,
                           COALESCE(SUM(te.minutes * COALESCE(
                               lp.hourly_rate, ls.default_hourly_rate, :fallback_rate
                           ) / 60.0), 0) AS amount
                    FROM time_entries te
                    LEFT JOIN leave_profiles lp
                           ON lp.org_id = te.org_id AND lp.user_id = te.user_id
                    LEFT JOIN leave_settings ls ON ls.org_id = te.org_id
                    WHERE {clauses}
                    """  # noqa: S608 - static clauses, bound params only
                ),
                params,
            )
        ).mappings().one()
        return {
            "count": int(row["cnt"]),
            "minutes": int(row["minutes"]),
            "amount": Decimal(row["amount"]).quantize(Decimal("0.01")),
        }

    async def _unbilled_clauses(
        self,
        company_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None,
        until: date | None,
    ) -> tuple[str, dict[str, Any]]:
        """The time module's "to invoice" predicate, scoped to one client — written once so
        the list, the totals and the re-validation on write cannot disagree about what is
        billable (they were three near-copies, and a fourth was already drifting)."""
        clauses = _TO_INVOICE + " AND te.company_id = :cid"
        params: dict[str, Any] = {"oid": self.ctx.org.id, "cid": company_id}
        if project_id is not None:
            clauses += " AND te.project_id = :pid"
            params["pid"] = project_id
        if until is not None:
            clauses += " AND te.started_at < :until"
            params["until"] = datetime.combine(
                until + date.resolution, datetime.min.time(),
                tzinfo=await org_zoneinfo(self.ctx.session, self.ctx.org.id),
            )
        return clauses, params

    async def _unbilled_rows(
        self,
        company_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None,
        until: date | None,
        limit: int | None = None,
    ) -> list[Any]:
        # Bare-table reads over published columns (§6): the time module's "to invoice" set is
        # approved AND billable AND not invoiced (time/models.py), joined for display names.
        # ``employee_rate`` is the logger's effective rate (#226: personal → leave org
        # default) — the rate the client is billed at; there is no project rate.
        clauses, params = await self._unbilled_clauses(
            company_id, project_id=project_id, until=until
        )
        bound = ""
        if limit is not None:
            bound = " LIMIT :lim"
            params["lim"] = limit
        stmt = text(
            f"""
            SELECT te.id, te.started_at, te.minutes, te.description, te.project_id,
                   p.name AS project_name,
                   COALESCE(lp.hourly_rate, ls.default_hourly_rate) AS employee_rate,
                   u.full_name AS user_name
            FROM time_entries te
            LEFT JOIN projects p ON p.id = te.project_id AND p.org_id = te.org_id
            LEFT JOIN leave_profiles lp
                   ON lp.org_id = te.org_id AND lp.user_id = te.user_id
            LEFT JOIN leave_settings ls ON ls.org_id = te.org_id
            LEFT JOIN users u ON u.id = te.user_id
            WHERE {clauses}
            ORDER BY te.started_at{bound}
            """  # noqa: S608 - static column clauses, bound params only
        )
        return list((await self.ctx.session.execute(stmt, params)).mappings().all())

    async def uninvoiced_report(self, *, group: str, limit: int) -> dict[str, Any]:
        """The org-wide "still to invoice" backlog (#277): the ``_unbilled_rows`` predicate
        without its company scope, bucketed server-side so the subtotals are exact whatever
        the entry cap. Read-only — building the invoice stays with ``from_time``.

        Two queries, like ``TimeService.report``: one ``GROUP BY`` over the whole set for
        the per-group figures, one capped row fetch for the expand/collapse detail. Date
        buckets live in the org's local calendar (§8) — an entry logged late on the 31st
        UTC belongs to the 1st where the org works.
        """
        # ``:any``, matching the route (#266): this is the org's whole unbilled backlog with
        # every employee's name and hourly rate on it — the invoicing module, not a document.
        self.ctx.require("invoicing.invoice.read", "any")
        group_expr = _UNINVOICED_GROUP_EXPR[group]
        # The detail always needs the org zone: each row carries its org-local calendar day.
        params: dict[str, Any] = {
            "oid": self.ctx.org.id,
            "tz": (await org_zoneinfo(self.ctx.session, self.ctx.org.id)).key,
        }
        if group in _UNINVOICED_DATE_GROUPS:
            label_expr, label_join = "NULL", ""
            # Chronological, oldest first — the longest-outstanding hours lead the page.
            group_order = f"({group_expr})"
        else:
            label_expr, label_join = _UNINVOICED_GROUP_LABEL[group]
            group_order = f"lower({label_expr}) ASC NULLS LAST, ({group_expr})"
        # The employee-rate chain of ``_unbilled_rows`` (#226), with the invoicing default
        # folded into the SQL so the aggregate prices exactly like the entry list.
        default_rate = (await self.settings.row()).default_hourly_rate or Decimal(0)
        params["fallback_rate"] = default_rate
        rate_expr = "COALESCE(lp.hourly_rate, ls.default_hourly_rate, :fallback_rate)"
        clauses = _TO_INVOICE
        rate_joins = """
            LEFT JOIN leave_profiles lp
                   ON lp.org_id = te.org_id AND lp.user_id = te.user_id
            LEFT JOIN leave_settings ls ON ls.org_id = te.org_id
        """
        grouped = list(
            (
                await self.ctx.session.execute(
                    text(
                        f"""
                        SELECT {group_expr} AS gkey, {label_expr} AS glabel,
                               COUNT(*) AS cnt,
                               SUM(te.minutes) AS minutes,
                               SUM(te.minutes * {rate_expr} / 60.0) AS amount
                        FROM time_entries te
                        {rate_joins}
                        {label_join}
                        WHERE {clauses}
                        GROUP BY 1, 2
                        ORDER BY {group_order}
                        """  # noqa: S608 - static fragments from module dicts, bound params
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        # The detail follows the group order, then time — so a hit cap truncates the tail
        # groups instead of scattering gaps through every section.
        rows = list(
            (
                await self.ctx.session.execute(
                    text(
                        f"""
                        SELECT te.id, te.started_at, te.minutes, te.description,
                               (te.started_at AT TIME ZONE :tz)::date AS entry_date,
                               te.company_id, c.name AS company_name,
                               te.project_id, p.name AS project_name,
                               u.full_name AS user_name,
                               {group_expr} AS gkey,
                               {rate_expr} AS rate
                        FROM time_entries te
                        LEFT JOIN companies c ON c.id = te.company_id AND c.org_id = te.org_id
                        LEFT JOIN projects p ON p.id = te.project_id AND p.org_id = te.org_id
                        LEFT JOIN users u ON u.id = te.user_id
                        {rate_joins}
                        WHERE {clauses}
                        ORDER BY {group_order}, te.started_at
                        LIMIT :limit
                        """  # noqa: S608 - static fragments from module dicts, bound params
                    ),
                    {**params, "limit": limit},
                )
            )
            .mappings()
            .all()
        )
        total_count = sum(g["cnt"] for g in grouped)
        return {
            "group": group,
            "groups": [
                {
                    "key": g["gkey"],
                    "label": g["glabel"],
                    "count": g["cnt"],
                    "minutes": int(g["minutes"]),
                    "amount": round_cents(Decimal(g["amount"])),
                }
                for g in grouped
            ],
            "entries": [
                {
                    "id": r["id"],
                    "group_key": r["gkey"],
                    "started_at": r["started_at"],
                    "entry_date": r["entry_date"],
                    "minutes": r["minutes"],
                    "description": r["description"],
                    "company_id": r["company_id"],
                    "company_name": r["company_name"],
                    "project_id": r["project_id"],
                    "project_name": r["project_name"],
                    "user_name": r["user_name"],
                    "rate": Decimal(r["rate"]),
                    "amount": round_cents(Decimal(r["minutes"]) * Decimal(r["rate"]) / 60),
                }
                for r in rows
            ],
            "total_minutes": sum(int(g["minutes"]) for g in grouped),
            # Rounded once on the exact sum (calc.py's rule) — never a sum of rounded parts.
            "total_amount": round_cents(sum((Decimal(g["amount"]) for g in grouped), Decimal(0))),
            "total_count": total_count,
            "truncated": total_count > len(rows),
        }

    async def from_time(self, data: InvoiceFromTime) -> Invoice:
        """Build a draft invoice from unbilled time and stamp those entries as invoiced —
        remembering which (``invoice_time_entries``), so deleting the draft un-bills exactly
        them and nothing else."""
        self.ctx.require("invoicing.invoice.write")
        rows = await self._unbilled_rows(
            data.company_id, project_id=data.project_id, until=data.until
        )
        if not rows:
            raise AppError("validation", "errors.invoicing.no_unbilled", status_code=400)
        settings_row = await self.settings.row()
        fallback_rate = (
            data.hourly_rate
            if data.hourly_rate is not None
            else settings_row.default_hourly_rate
        ) or Decimal(0)

        def rate_for(row: Any) -> Decimal:
            # Manual per-build override → the logger's effective rate (#226) → the org's
            # invoicing default, the last resort when no employee rate is configured.
            if data.hourly_rate is not None:
                return data.hourly_rate
            if row["employee_rate"] is not None:
                return Decimal(row["employee_rate"])
            return Decimal(fallback_rate)

        def hours(minutes: int) -> Decimal:
            return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))

        # The unit reads in the **document's** language, not the caller's: an invoice to a
        # German client says "Std.", and it says so whoever pressed the button.
        _, org_locale = await _org_defaults(self.ctx)
        unit = translate("invoicing.unit.hour", org_locale)

        # Grouped lines key on the rate as well: two people on one project may bill at two
        # rates (#226), and a group priced at its first entry's rate would misbill the rest.
        # Each line also carries **which entries it covers**, so a later edit of this draft
        # releases exactly the hours whose line went and no others.
        lines: list[LineWrite] = []
        if data.group_by == "project":
            groups: dict[Any, dict[str, Any]] = {}
            for row in rows:
                rate = rate_for(row)
                bucket = groups.setdefault(
                    (row["project_id"], rate),
                    {"name": row["project_name"], "minutes": 0, "rate": rate, "ids": []},
                )
                bucket["minutes"] += row["minutes"]
                bucket["ids"].append(row["id"])
            for bucket in groups.values():
                lines.append(
                    LineWrite(
                        description=bucket["name"] or translate("invoicing.unit.hours", org_locale),
                        line_kind=LineKind.HOURS,
                        quantity=hours(bucket["minutes"]),
                        unit=unit,
                        unit_price=bucket["rate"],
                        time_entry_ids=bucket["ids"],
                    )
                )
        elif data.group_by == "day":
            by_day: dict[tuple[Any, Any, Any], dict[str, Any]] = {}
            for row in rows:
                day = row["started_at"].date()
                rate = rate_for(row)
                bucket = by_day.setdefault(
                    (day, row["project_id"], rate),
                    {
                        "day": day, "name": row["project_name"], "minutes": 0,
                        "rate": rate, "ids": [],
                    },
                )
                bucket["minutes"] += row["minutes"]
                bucket["ids"].append(row["id"])
            for bucket in by_day.values():
                label = bucket["day"].strftime("%d-%m-%Y")
                name = f"{bucket['name']} · {label}" if bucket["name"] else label
                lines.append(
                    LineWrite(
                        description=name,
                        line_kind=LineKind.HOURS,
                        quantity=hours(bucket["minutes"]),
                        unit=unit,
                        unit_price=bucket["rate"],
                        time_entry_ids=bucket["ids"],
                    )
                )
        else:  # entry
            for row in rows:
                label = row["started_at"].date().strftime("%d-%m-%Y")
                description = (
                    row["description"]
                    or row["project_name"]
                    or translate("invoicing.unit.hours", org_locale)
                )
                lines.append(
                    LineWrite(
                        description=f"{label} · {description}"[:512],
                        line_kind=LineKind.HOURS,
                        quantity=hours(row["minutes"]),
                        unit=unit,
                        unit_price=rate_for(row),
                        time_entry_ids=[row["id"]],
                    )
                )

        # ``create`` links the entries the lines name — the rows came from ``_unbilled_rows``,
        # so all of them are still billable and none is skipped.
        return await self.create(InvoiceCreate(company_id=data.company_id, lines=lines))

    async def _reconcile_time_entries(
        self, invoice: Invoice, lines: Sequence[LineWrite]
    ) -> None:
        """Make the invoice bill exactly the entries its **lines** now say it bills.

        The counterpart of ``_claim_periods`` for hours, and the half that was missing:
        ``create`` linked entries but ``update`` never did, so removing an hours line from a
        draft left the entry stamped invoiced with no line billing it — permanently unbillable
        without a database edit — and adding one never billed it at all.

        Entries no line claims any more are released; new ones are linked. The **legacy
        guard** is the one subtlety: a draft written before lines carried provenance has hours
        lines and no ids on them, and reading that as "no line claims anything" would release
        links the document is still billing. So a document whose hours lines are all
        provenance-free keeps its links untouched — it is described by the invoice-level table
        exactly as it always was, until someone edits its hours, at which point the lines
        speak for themselves.
        """
        wanted: list[uuid.UUID] = list(
            dict.fromkeys(eid for line in lines for eid in line.time_entry_ids)
        )
        links = self.ctx.repo(InvoiceTimeEntry)
        existing = list(
            await self.ctx.session.scalars(
                links.scoped_select().where(InvoiceTimeEntry.invoice_id == invoice.id)
            )
        )
        legacy = not wanted and any(
            line.line_kind == LineKind.HOURS for line in lines
        )
        if existing and not legacy:
            stale = [row for row in existing if row.time_entry_id not in set(wanted)]
            if stale:
                await self.ctx.session.execute(
                    text(
                        "UPDATE time_entries SET invoiced_at = NULL"
                        " WHERE org_id = :oid AND id IN :ids"
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"oid": self.ctx.org.id, "ids": [r.time_entry_id for r in stale]},
                )
                for row in stale:
                    await links.delete(row)
                await self.ctx.session.flush()
        held = {row.time_entry_id for row in existing} if legacy else {
            row.time_entry_id for row in existing if row.time_entry_id in set(wanted)
        }
        await self._link_time_entries(
            invoice, [eid for eid in wanted if eid not in held]
        )

    async def _link_time_entries(
        self, invoice: Invoice, time_entry_ids: Sequence[uuid.UUID]
    ) -> None:
        """Bill exactly these time entries onto the (draft) invoice — the mirror of
        ``_release_time_entries``: create the ``invoice_time_entries`` links, stamp
        ``invoiced_at``, and record ``time_attached``, all in the writing transaction.

        Every id is re-validated against the same set ``_unbilled_rows`` selects (this org,
        this invoice's company, approved + billable + ended + not-yet-invoiced): a stale
        form, a foreign id, or a double-submit can only ever bill *less*, never 500 on the
        ``uq_invoice_time_entries_entry`` constraint (``invoiced_at IS NULL`` ⇔ unlinked)."""
        wanted = list(dict.fromkeys(eid for eid in time_entry_ids if eid is not None))
        if not wanted:
            return
        valid = set(
            (
                await self.ctx.session.execute(
                    text(
                        f"""
                        SELECT te.id FROM time_entries te
                        WHERE {_TO_INVOICE}
                          AND te.company_id = :cid AND te.id IN :ids
                        """  # noqa: S608 - module constant + static clauses, bound params only
                    ).bindparams(bindparam("ids", expanding=True)),
                    {"oid": self.ctx.org.id, "cid": invoice.company_id, "ids": wanted},
                )
            ).scalars()
        )
        entry_ids = [eid for eid in wanted if eid in valid]
        if not entry_ids:
            return
        links = self.ctx.repo(InvoiceTimeEntry)
        for entry_id in entry_ids:
            await links.create(invoice_id=invoice.id, time_entry_id=entry_id)
        # Stamp through the published column, tenant-scoped (§6) — "invoiced implies
        # approved" already holds because the selection required approved_at.
        await self.ctx.session.execute(
            text(
                "UPDATE time_entries SET invoiced_at = now()"
                " WHERE org_id = :oid AND id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"oid": self.ctx.org.id, "ids": entry_ids},
        )
        await ActivityService(self.ctx).record(
            self.entity_type, invoice.id, "time_attached", {"entries": len(entry_ids)}
        )

    async def outstanding(self, company_id: uuid.UUID) -> dict[str, Any]:
        """Everything this client still has to be invoiced for — the picker's whole source.

        Three buckets in one round trip, because the editor has three sections and the dialog
        opens on all of them at once. Each bucket's *what is owed* half comes from the module
        that owns the agreement, through its published interface (§6) — they own the interval
        vocabulary and the "price valid at the period boundary" rule, so a hand-picked line and
        a cron-raised one bill the same money by construction. What this module adds is the
        half it owns: whether a period is already claimed by a document.

        Claimed periods are **shown, not hidden** (``already_billed``), because "did I already
        invoice March?" is the question the picker exists to answer, and answering it by
        omission produces a duplicate a week later.
        """
        self.ctx.require("invoicing.invoice.write")
        from app.modules.domains.service import DomainService
        from app.modules.subscriptions.service import SubscriptionService

        await _company_row(self.ctx, company_id)
        agreements = await SubscriptionService(self.ctx).open_agreements(company_id)
        renewals = await DomainService(self.ctx).open_renewals(company_id)
        return {
            "hours": await self.unbilled(company_id),
            "subscriptions": await self._with_claims(
                agreements,
                spec=_CLAIM_SOURCES[0],
                key=lambda a: a.subscription_id,
                extra=lambda a: {"interval": a.interval},
            ),
            "domains": await self._with_claims(
                agreements=renewals,
                spec=_CLAIM_SOURCES[1],
                key=lambda d: d.domain_id,
                extra=lambda d: {"no_price": d.no_price, "invoiceable": d.invoiceable},
            ),
        }

    async def _with_claims(
        self,
        agreements: Sequence[Any],
        *,
        spec: _ClaimSource,
        key: Callable[[Any], uuid.UUID],
        extra: Callable[[Any], dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Mark every offered period with whether a document already holds it.

        One read for the whole set, never one per agreement or (worse) one per period: an
        agency with twenty domains three years behind would otherwise cost sixty queries to
        open a dialog (docs/PERFORMANCE.md).
        """
        if not agreements:
            return []
        claims = self.ctx.repo(spec.model)
        source_col = getattr(spec.model, spec.column)
        held = {
            (getattr(row, spec.column), row.period_end)
            for row in await self.ctx.session.scalars(
                claims.scoped_select().where(source_col.in_([key(a) for a in agreements]))
            )
        }
        return [
            {
                "id": key(agreement),
                "name": agreement.name,
                "currency": agreement.currency,
                "amount": agreement.amount,
                "truncated": agreement.truncated,
                "no_cycle": agreement.no_cycle,
                "periods": [
                    {
                        "period_start": period.period_start,
                        "period_end": period.period_end,
                        "amount": period.amount,
                        "future": period.future,
                        "lines": [
                            {
                                "description": description,
                                "quantity": quantity,
                                "unit_price": price,
                            }
                            for description, quantity, price in period.lines
                        ],
                        "already_billed": (key(agreement), period.period_end) in held,
                    }
                    for period in agreement.periods
                ],
                **extra(agreement),
            }
            for agreement in agreements
        ]

    async def _claim_periods(self, invoice: Invoice, lines: Sequence[LineWrite]) -> None:
        """Record which billing periods this invoice bills, so their crons skip them.

        Owner's rule: *"the cron should know it is already paid."* ``on_subscription_due`` and
        ``on_domain_due`` consult these tables before drafting, so a period billed by hand — on
        a mixed invoice with hours and products next to it — is never billed a second time
        when the cycle comes round. The unique key is ``(org, source, period_end)``: a period
        belongs to exactly one invoice, and a second document trying to claim it is refused
        rather than silently double-billing a client.

        Two sources, one rule, because a renewal and a retainer differ only in which table
        the id points at: a client's year-end invoice carries eleven domains beside three
        agreements, and each has to stop its own cron.

        The claim is rebuilt from the lines on every write, so removing a line gives its
        period back. A source that isn't this org's, or isn't this invoice's client, claims
        nothing — the "bill less, never guess" stance ``_link_time_entries`` takes.
        """
        for spec in _CLAIM_SOURCES:
            await self._claim_one_source(invoice, lines, spec)

    async def _claim_one_source(
        self, invoice: Invoice, lines: Sequence[LineWrite], spec: _ClaimSource
    ) -> None:
        claims = self.ctx.repo(spec.model)
        source_col = getattr(spec.model, spec.column)
        existing = list(
            await self.ctx.session.scalars(
                claims.scoped_select().where(spec.model.invoice_id == invoice.id)
            )
        )
        wanted: dict[tuple[uuid.UUID, date], LineWrite] = {}
        for line in lines:
            source_id = getattr(line, spec.column)
            if source_id is not None and line.period_end is not None:
                wanted[(source_id, line.period_end)] = line

        # A draft written before lines carried provenance holds recurring lines that name no
        # period. Reading that as "nothing is claimed" would release a claim the document is
        # still billing, and the cron would raise the month again — the very bug provenance
        # exists to close, re-entered through the upgrade. So an unattributed document keeps
        # its claims until someone edits the lines that carry them.
        #
        # ``SUBSCRIPTION`` for **both** sources, and deliberately: a domain renewal is stamped
        # with that kind too (``events.py`` — a cycle raises recurring lines by definition, and
        # a renewal prints in the document's subscription band). There is no ``domain`` kind to
        # look for and adding one would buy a distinction no reader has asked for.
        legacy = not wanted and any(
            line.line_kind == LineKind.SUBSCRIPTION for line in lines
        )
        if legacy:
            return

        if wanted:
            valid = set(
                (
                    await self.ctx.session.execute(
                        text(
                            f"""
                            SELECT s.id FROM {spec.table} s
                            WHERE s.org_id = :oid AND s.company_id = :cid AND s.id IN :ids
                            """  # noqa: S608 - table name from a module constant, bound params
                        ).bindparams(bindparam("ids", expanding=True)),
                        {
                            "oid": self.ctx.org.id,
                            "cid": invoice.company_id,
                            "ids": [sid for sid, _ in wanted],
                        },
                    )
                ).scalars()
            )
            wanted = {key: line for key, line in wanted.items() if key[0] in valid}

        for row in existing:
            if (getattr(row, spec.column), row.period_end) not in wanted:
                await claims.delete(row)
        held = {(getattr(row, spec.column), row.period_end) for row in existing}
        fresh = {key: line for key, line in wanted.items() if key not in held}
        if not fresh:
            return

        # Someone else's claim on the same period: refuse the write rather than let the
        # unique index 500, and name the conflict so the form can say which line is the
        # problem. Flush first — the check must see this transaction's own deletes.
        await self.ctx.session.flush()
        taken = await self.ctx.session.scalar(
            claims.scoped_select()
            .where(tuple_(source_col, spec.model.period_end).in_(list(fresh)))
            .limit(1)
        )
        if taken is not None:
            raise AppError(
                "conflict",
                "errors.invoicing.period_already_billed",
                status_code=409,
                fields={"lines": "errors.invoicing.period_already_billed"},
            )
        for (source_id, period_end), line in fresh.items():
            await claims.create(
                invoice_id=invoice.id,
                **{spec.column: source_id},
                period_start=line.period_start,
                period_end=period_end,
            )

    async def _release_subscription_periods(self, invoice_id: uuid.UUID) -> None:
        """Give this invoice's claimed periods back to the crons (delete/cancel path)."""
        for spec in _CLAIM_SOURCES:
            claims = self.ctx.repo(spec.model)
            for row in await self.ctx.session.scalars(
                claims.scoped_select().where(spec.model.invoice_id == invoice_id)
            ):
                await claims.delete(row)

    async def _release_time_entries(self, invoice_id: uuid.UUID) -> None:
        """Un-bill exactly the entries this invoice billed (delete/cancel path)."""
        links = self.ctx.repo(InvoiceTimeEntry)
        rows = list(
            await self.ctx.session.scalars(
                links.scoped_select().where(InvoiceTimeEntry.invoice_id == invoice_id)
            )
        )
        if not rows:
            return
        await self.ctx.session.execute(
            text(
                "UPDATE time_entries SET invoiced_at = NULL"
                " WHERE org_id = :oid AND id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)),
            {"oid": self.ctx.org.id, "ids": [r.time_entry_id for r in rows]},
        )
        for row in rows:
            await links.delete(row)

    async def _revert_quote(self, invoice: Invoice) -> None:
        """Deleting the draft an accepted quote became puts the quote back to accepted."""
        if invoice.quote_id is None:
            return
        quote = await self.ctx.repo(Quote).get(invoice.quote_id)
        if quote is not None and quote.status == QuoteStatus.INVOICED.value:
            await self.ctx.repo(Quote).update(
                quote, status=QuoteStatus.ACCEPTED.value, invoice_id=None
            )

    # --- summary ---------------------------------------------------------------- #
    async def summary(self) -> dict[str, Any]:
        """The list-header tiles, in org currency (foreign documents convert through their
        stored rate; 1 when unset). Approximate for steering — documents stay exact.

        Hand-written SQL (six conditional aggregates in one pass), so it cannot ride
        ``scoped_select`` and had no company horizon: a membership restricted to one company
        group read *"2 concepten"* above a list showing one, which is the count leak #252 named
        and #285 found here — a tile is a fact about clients it cannot see. The scope is spliced
        as a bound ``IN``; an empty horizon short-circuits, since ``IN ()`` is not valid SQL and
        the honest answer is all-zeroes anyway.
        """
        today = await org_today(self.ctx)
        base = "COALESCE(exchange_rate, 1)"
        scope = self.ctx.company_scope
        # An external login reads its own open/overdue/paid figures — "what do I still owe"
        # is the one number a client page is for — but never a draft count, and never the
        # quote figures: quotes are out of #266's scope entirely and `invoicing.quote.read`
        # stays staff-only, so counting them here would answer a question the API refuses.
        drafts_visible = not self.issued_only
        quotes_visible = self.ctx.can("invoicing.quote.read")
        if scope is not None and not scope:
            return {
                "open_count": 0, "open_total": 0.0,
                "overdue_count": 0, "overdue_total": 0.0,
                "draft_count": 0, "paid_this_year": 0.0,
                "quotes_open_count": 0, "quotes_open_total": 0.0,
            }
        horizon_sql = "" if scope is None else " AND company_id IN :companies"
        params: dict[str, Any] = {"oid": self.ctx.org.id}
        if scope is not None:
            params["companies"] = list(scope)

        def _scoped_text(sql: str):  # noqa: ANN202 — a bound `IN` needs the expanding param
            stmt = text(sql)
            return stmt if scope is None else stmt.bindparams(
                bindparam("companies", expanding=True)
            )

        row = (
            await self.ctx.session.execute(
                _scoped_text(
                    f"""
                    SELECT
                      COUNT(*) FILTER (WHERE status = 'open') AS open_count,
                      COALESCE(SUM((total - paid_total) * {base})
                               FILTER (WHERE status = 'open'), 0) AS open_total,
                      COUNT(*) FILTER (WHERE status = 'open' AND due_date < :today)
                        AS overdue_count,
                      COALESCE(SUM((total - paid_total) * {base})
                               FILTER (WHERE status = 'open' AND due_date < :today), 0)
                        AS overdue_total,
                      COUNT(*) FILTER (WHERE status = 'draft') AS draft_count,
                      COALESCE(SUM(total * {base})
                               FILTER (WHERE status = 'paid'
                                       AND EXTRACT(YEAR FROM paid_at) = :year), 0)
                        AS paid_this_year
                    FROM invoices WHERE org_id = :oid{horizon_sql}
                    """  # noqa: S608 - f-string splices the constant COALESCE expr and a
                    # bound-parameter `IN`, never a value
                ),
                {**params, "today": today, "year": today.year},
            )
        ).mappings().one()
        # Skipped, not merely blanked: a caller who cannot read quotes does not pay for the
        # query either. Same reason the list skips lines it will not draw (#290).
        quotes: Any = {"open_count": 0, "open_total": 0}
        if quotes_visible:
            quotes = (
                await self.ctx.session.execute(
                    _scoped_text(
                        f"""
                    SELECT COUNT(*) AS open_count,
                           COALESCE(SUM(total * {base}), 0) AS open_total
                    FROM quotes WHERE org_id = :oid AND status = 'open'{horizon_sql}
                    """  # noqa: S608
                    ),
                    params,
                )
            ).mappings().one()
        return {
            "open_count": row["open_count"],
            "open_total": round(float(row["open_total"]), 2),
            "overdue_count": row["overdue_count"],
            "overdue_total": round(float(row["overdue_total"]), 2),
            "draft_count": row["draft_count"] if drafts_visible else 0,
            "paid_this_year": round(float(row["paid_this_year"]), 2),
            "quotes_open_count": quotes["open_count"],
            "quotes_open_total": round(float(quotes["open_total"]), 2),
        }


# --------------------------------------------------------------------------- #
# Quotes
# --------------------------------------------------------------------------- #
class QuoteService(_DocumentService):
    model = Quote
    line_model = QuoteLine
    line_fk = "quote_id"
    entity_type = ENTITY_QUOTE
    audited_fields = _AUDITED_QUOTE_FIELDS
    post_issue_fields = _POST_ISSUE_QUOTE_FIELDS

    async def list(
        self,
        *,
        limit: int,
        offset: int,
        status: str | None = None,
        company_id: uuid.UUID | None = None,
        q: str | None = None,
        sort: str | None = None,
        lines: bool = True,
    ) -> tuple[Sequence[Quote], int]:
        conditions = []
        if status:
            conditions.append(Quote.status == status)
        if company_id is not None:
            conditions.append(Quote.company_id == company_id)
        if q:
            needle = f"%{q.strip()}%"
            conditions.append(Quote.number.ilike(needle) | Quote.reference.ilike(needle))
        stmt = self.repo.scoped_select().where(*conditions)
        stmt = apply_sort(stmt, sort, QUOTE_SORTABLE, default=Quote.created_at.desc())
        items = list(
            (await self.ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        )
        total = int(
            await self.ctx.session.scalar(
                # Horizon-carrying, like the invoice list's (#285).
                self.repo.scoped_count_select().where(*conditions)
            )
            or 0
        )
        await self._attach(items, lines=lines)
        return items, total

    async def get(self, quote_id: uuid.UUID) -> Quote:
        quote = await self.repo.get_or_404(quote_id)
        await self._attach([quote])
        return quote

    async def for_company(self, company_id: uuid.UUID, *, limit: int = 5) -> Sequence[Quote]:
        stmt = (
            self.repo.scoped_select()
            .where(Quote.company_id == company_id)
            .order_by(Quote.created_at.desc())
            .limit(limit)
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # The company panel lists number/date/status/total, never a line (#290).
        await self._attach(items, lines=False)
        return items

    async def _attach(self, quotes: Sequence[Quote], *, lines: bool = True) -> None:
        """``lines=False`` is the list's opt-out — see ``InvoiceService._attach`` (#290)."""
        if not quotes:
            return
        names = await self._company_names(quotes)
        lines_by_doc = await self._doc_lines([q.id for q in quotes]) if lines else {}
        today = await org_today(self.ctx)
        for quote in quotes:
            rows = lines_by_doc.get(quote.id, [])
            quote.company_name = names.get(quote.company_id, "")  # type: ignore[attr-defined]
            quote.lines = rows  # type: ignore[attr-defined]
            quote.tax_groups = (  # type: ignore[attr-defined]
                [
                    {
                        "rate_pct": g.rate_pct, "category": g.category, "name": g.name,
                        "base": g.base, "tax": g.tax,
                    }
                    for g in _totals_from_rows(
                        rows, prices_include_tax=quote.prices_include_tax
                    ).groups
                ]
                if lines
                else []
            )
            quote.expired = (  # type: ignore[attr-defined]
                quote.status in (QuoteStatus.OPEN.value, QuoteStatus.EXPIRED.value)
                and quote.valid_until is not None
                and quote.valid_until < today
            ) or quote.status == QuoteStatus.EXPIRED.value

    async def create(self, data: QuoteCreate) -> Quote:
        self.ctx.require("invoicing.quote.write")
        company = await _company_row(self.ctx, data.company_id)
        contact_email, contact_name = (
            await _contact_party(self.ctx, data.contact_id)
            if data.contact_id
            else (None, None)
        )
        if data.template_id is not None:
            await _ensure_template(self.ctx, data.template_id)
        settings_row = await self.settings.row()
        currency, locale = await _org_defaults(self.ctx)
        doc_locale = data.locale or locale
        include_tax = (
            data.prices_include_tax
            if data.prices_include_tax is not None
            else settings_row.prices_include_tax
        )
        custom = await self.custom_fields.validate(self.entity_type, data.custom or {})
        line_rows = await _snapshot_lines(
            self.ctx, data.lines, locale=doc_locale,
            default_tax_rate_id=await self._default_tax_rate_id(settings_row),
        )
        quote = await self.repo.create(
            company_id=data.company_id,
            contact_id=data.contact_id,
            customer=_customer_snapshot(company, email=contact_email, attn=contact_name),
            currency=(data.currency or currency).upper(),
            exchange_rate=data.exchange_rate,
            locale=doc_locale,
            reference=data.reference,
            intro=data.intro,
            # Markdown source (issue #66/#228): raw HTML is stripped on write.
            notes=sanitize_markdown(data.notes),
            template_id=data.template_id or settings_row.default_template_id,
            issue_date=data.issue_date,
            valid_until=data.valid_until,
            prices_include_tax=include_tax,
            custom=custom,
        )
        await self._replace_lines(quote, line_rows)
        await ActivityService(self.ctx).record_created(self.entity_type, quote.id)
        await self._attach([quote])
        return quote

    async def update(self, quote_id: uuid.UUID, data: QuoteUpdate) -> Quote:
        self.ctx.require("invoicing.quote.write")
        quote = await self.repo.get_or_404(quote_id)
        before = snapshot(quote, self.audited_fields)
        sent = data.model_dump(exclude_unset=True)
        if quote.status not in (QuoteStatus.DRAFT.value, QuoteStatus.OPEN.value):
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        if quote.status == QuoteStatus.OPEN.value:
            locked = set(sent) - self.post_issue_fields
            if locked:
                raise AppError("conflict", "errors.invoicing.locked", status_code=409)

        values: dict[str, Any] = {}
        for field in ("reference", "intro", "notes", "issue_date", "valid_until",
                      "exchange_rate"):
            if field in sent:
                values[field] = sent[field]
        if "notes" in values:
            values["notes"] = sanitize_markdown(values["notes"])
        if "locale" in sent and data.locale is not None:
            values["locale"] = data.locale
        if "currency" in sent and data.currency is not None:
            values["currency"] = data.currency
        if "prices_include_tax" in sent and data.prices_include_tax is not None:
            values["prices_include_tax"] = data.prices_include_tax
        if "contact_id" in sent:
            if data.contact_id is not None:
                email, name = await _contact_party(self.ctx, data.contact_id)
                customer = dict(quote.customer)
                customer["email"] = email or customer.get("email")
                customer["attn"] = name
                values["customer"] = customer
            values["contact_id"] = data.contact_id
        if "template_id" in sent:
            if data.template_id is not None:
                await _ensure_template(self.ctx, data.template_id)
            values["template_id"] = data.template_id
        if "custom" in sent:
            values["custom"] = await self.custom_fields.validate(
                self.entity_type, data.custom or {}
            )
        quote = await self.repo.update(quote, **values)

        if data.lines is not None:
            settings_row = await self.settings.row()
            line_rows = await _snapshot_lines(
                self.ctx, data.lines, locale=quote.locale,
                default_tax_rate_id=await self._default_tax_rate_id(settings_row),
            )
            await self._replace_lines(quote, line_rows)

        await ActivityService(self.ctx).record_update(
            self.entity_type, quote.id, before, snapshot(quote, self.audited_fields)
        )
        await self._attach([quote])
        return quote

    async def delete(self, quote_id: uuid.UUID) -> None:
        self.ctx.require("invoicing.quote.delete")
        quote = await self.repo.get_or_404(quote_id)
        if quote.status not in (
            QuoteStatus.DRAFT.value, QuoteStatus.REJECTED.value, QuoteStatus.EXPIRED.value
        ):
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        await self.repo.delete(quote)

    async def issue(self, quote_id: uuid.UUID, data: InvoiceIssue) -> Quote:
        self.ctx.require("invoicing.quote.write")
        quote = await self.repo.get_or_404(quote_id)
        if quote.status != QuoteStatus.DRAFT.value:
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        if await self.lines.count(**{self.line_fk: quote.id}) == 0:
            raise AppError("validation", "errors.invoicing.no_lines", status_code=400)
        settings_row = await self.settings.row()
        if not (settings_row.company_details or {}).get("name"):
            raise AppError(
                "validation", "errors.invoicing.seller_incomplete", status_code=400
            )
        today = await org_today(self.ctx)
        issue_date = data.issue_date or quote.issue_date or today
        valid_until = data.due_date or quote.valid_until or (
            issue_date + timedelta(days=settings_row.quote_valid_days)
        )
        company = await _company_row(self.ctx, quote.company_id)
        snapshot = quote.customer or {}
        email, attn = snapshot.get("email"), snapshot.get("attn")
        number = await self.settings.allocate_number("quote")
        quote = await self.repo.update(
            quote,
            number=number,
            status=QuoteStatus.OPEN.value,
            issue_date=issue_date,
            valid_until=valid_until,
            customer=_customer_snapshot(company, email=email, attn=attn),
        )
        await ActivityService(self.ctx).record(
            self.entity_type, quote.id, "issued", {"number": number}
        )
        await self._attach([quote])
        return quote

    async def decide(self, quote_id: uuid.UUID, accepted: bool, data: QuoteDecision) -> Quote:
        self.ctx.require("invoicing.quote.write")
        quote = await self.repo.get_or_404(quote_id)
        if quote.status not in (QuoteStatus.OPEN.value, QuoteStatus.EXPIRED.value):
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        quote = await self.repo.update(
            quote,
            status=QuoteStatus.ACCEPTED.value if accepted else QuoteStatus.REJECTED.value,
            decided_at=datetime.now(UTC),
            decision_note=data.note,
        )
        await ActivityService(self.ctx).record(
            self.entity_type, quote.id, "accepted" if accepted else "rejected",
            {"note": data.note} if data.note else None,
        )
        await self._attach([quote])
        return quote

    async def send(self, quote_id: uuid.UUID, data: DocumentSend) -> Quote:
        self.ctx.require("invoicing.quote.send")
        quote = await self.repo.get_or_404(quote_id)
        if quote.status != QuoteStatus.OPEN.value:
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        to = data.to or (quote.customer or {}).get("email")
        if data.email:
            if not to:
                raise AppError(
                    "validation", "errors.invoicing.no_recipient", status_code=400
                )
            from app.core.email.branding import load_brand
            from app.core.email.senders import EmailAttachment
            from app.modules.invoicing import emails

            brand = await load_brand(self.ctx.session, self.ctx.org)
            message = emails.compose_quote_email(quote, brand.brand_name, data.message)
            message.to = to
            await self._attach([quote])
            content, filename = await self.document_pdf(quote, "quote")
            message.attachments.append(
                EmailAttachment(filename=filename, content=content, mimetype="application/pdf")
            )
            await emails.deliver(self.ctx, message, brand=brand)
        quote = await self.repo.update(quote, sent_at=datetime.now(UTC))
        await ActivityService(self.ctx).record(
            self.entity_type, quote.id, "sent",
            {"to": to} if data.email else {"external": True},
        )
        await self._attach([quote])
        return quote

    async def convert(self, quote_id: uuid.UUID) -> Invoice:
        """Accepted quote → draft invoice, carrying the lines *with their snapshots*: the
        deal keeps the prices and tax it was accepted at, whatever changed since."""
        self.ctx.require("invoicing.quote.write")
        self.ctx.require("invoicing.invoice.write")
        quote = await self.repo.get_or_404(quote_id)
        if quote.status != QuoteStatus.ACCEPTED.value:
            raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
        invoices = InvoiceService(self.ctx)
        invoice = await invoices.repo.create(
            company_id=quote.company_id,
            contact_id=quote.contact_id,
            customer=dict(quote.customer),
            currency=quote.currency,
            exchange_rate=quote.exchange_rate,
            locale=quote.locale,
            reference=quote.reference,
            intro=quote.intro,
            notes=quote.notes,
            template_id=quote.template_id,
            prices_include_tax=quote.prices_include_tax,
            quote_id=quote.id,
        )
        source_lines = (await self._doc_lines([quote.id])).get(quote.id, [])
        line_rows = [
            {
                "position": row.position,
                # The quote's own grouping carries over — the client accepted that document.
                "line_kind": row.line_kind,
                "description": row.description,
                "quantity": row.quantity,
                "unit": row.unit,
                "unit_price": row.unit_price,
                "tax_rate_id": row.tax_rate_id,
                "tax_rate_pct": row.tax_rate_pct,
                "tax_name": row.tax_name,
                "tax_category": row.tax_category,
                "amount": row.amount,
            }
            for row in source_lines
        ]
        await invoices._replace_lines(invoice, line_rows)
        await self.repo.update(quote, status=QuoteStatus.INVOICED.value, invoice_id=invoice.id)
        await ActivityService(self.ctx).record_created(ENTITY_INVOICE, invoice.id)
        await ActivityService(self.ctx).record(
            self.entity_type, quote.id, "converted", {"invoice_id": str(invoice.id)}
        )
        await invoices._attach([invoice], payments=True)
        return invoice


class ExternalRefService:
    """The accounting seam's bookkeeping (#31): idempotent upsert per
    ``(provider, local_type, local_id)`` — a retried export can only ever update."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(ExternalRef)

    async def list_for(self, local_type: str, local_id: uuid.UUID) -> Sequence[ExternalRef]:
        return list(
            await self.ctx.session.scalars(
                self.repo.scoped_select().where(
                    ExternalRef.local_type == local_type, ExternalRef.local_id == local_id
                )
            )
        )

    async def upsert(
        self,
        *,
        provider: str,
        local_type: str,
        local_id: uuid.UUID,
        external_id: str,
        payload: dict[str, Any] | None = None,
    ) -> ExternalRef:
        existing = await self.ctx.session.scalar(
            self.repo.scoped_select().where(
                ExternalRef.provider == provider,
                ExternalRef.local_type == local_type,
                ExternalRef.local_id == local_id,
            )
        )
        values = {
            "external_id": external_id,
            "synced_at": datetime.now(UTC),
            "payload": payload or {},
        }
        if existing is not None:
            return await self.repo.update(existing, **values)
        return await self.repo.create(
            provider=provider, local_type=local_type, local_id=local_id, **values
        )
