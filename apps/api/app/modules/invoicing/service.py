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

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, func, select, text

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.customfields import CustomFieldsService
from app.core.events import emit
from app.core.models import OrgSettings
from app.core.sorting import apply_sort
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.invoicing.calc import LineInput, Totals, compute_totals, line_amount
from app.modules.invoicing.models import (
    DocumentTemplate,
    ExternalRef,
    Invoice,
    InvoiceKind,
    InvoiceLine,
    InvoicePayment,
    InvoiceStatus,
    InvoiceTimeEntry,
    InvoicingSettings,
    Product,
    Quote,
    QuoteLine,
    QuoteStatus,
    TaxRate,
)
from app.modules.invoicing.numbering import format_number
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
    "subtotal", "total", "reminders_paused",
)
_AUDITED_QUOTE_FIELDS = (
    "status", "number", "company_id", "contact_id", "issue_date", "valid_until", "currency",
    "exchange_rate", "locale", "reference", "template_id", "prices_include_tax",
    "subtotal", "total",
)

#: Fields an issued (non-draft) document may still edit: rendering and process, never money.
_POST_ISSUE_INVOICE_FIELDS = frozenset(
    {"contact_id", "reference", "intro", "notes", "template_id", "locale", "due_date",
     "reminders_paused", "exchange_rate", "custom"}
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

#: Company columns a document snapshot copies (models.Company, issue #11).
_CUSTOMER_FIELDS = (
    "name", "address_line1", "address_line2", "postal_code", "city", "country",
    "vat_number", "coc_number",
)


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
            values["company_details"] = data.company_details.model_dump(mode="json")
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
        template = await self.repo.update(template, **values)
        if template.is_default:
            await self._make_sole_default(template)
        return template

    async def delete(self, template_id: uuid.UUID) -> None:
        self.ctx.require("invoicing.settings.manage")
        template = await self.repo.get_or_404(template_id)
        await self.repo.delete(template)

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
                " address_line2, postal_code, city, country"
                " FROM companies WHERE id = :cid AND org_id = :oid"
            ),
            {"cid": company_id, "oid": ctx.org.id},
        )
    ).mappings().first()
    if row is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return row


async def _contact_email(ctx: Any, contact_id: uuid.UUID) -> str | None:
    row = (
        await ctx.session.execute(
            text("SELECT id, email FROM contacts WHERE id = :cid AND org_id = :oid"),
            {"cid": contact_id, "oid": ctx.org.id},
        )
    ).mappings().first()
    if row is None:
        raise AppError(
            "validation", "errors.validation", status_code=400,
            fields={"contact_id": "errors.not_found"},
        )
    return row["email"]


def _customer_snapshot(company: Any, *, email: str | None) -> dict[str, Any]:
    data = {field: company[field] for field in _CUSTOMER_FIELDS}
    data["email"] = email or company["invoice_email"]
    return data


async def _snapshot_lines(
    ctx: Any,
    lines: list[LineWrite],
    *,
    locale: str,
    default_tax_rate_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    """Line rows ready to insert: tax resolved (scoped!) and frozen, amounts computed."""
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
        rows.append(
            {
                "position": index,
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
        )
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

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(self.model)
        self.lines = ctx.repo(self.line_model)
        self.settings = InvoicingSettingsService(ctx)
        self.custom_fields = CustomFieldsService(ctx)

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
    ) -> tuple[Sequence[Invoice], int]:
        conditions = []
        if status:
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
                select(func.count())
                .select_from(Invoice)
                .where(Invoice.org_id == self.ctx.org.id, *conditions)
            )
            or 0
        )
        await self._attach(items)
        return items, total

    async def get(self, invoice_id: uuid.UUID) -> Invoice:
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
        await self._attach(items)
        return items

    async def _attach(self, invoices: Sequence[Invoice], *, payments: bool = False) -> None:
        """Batch-resolve names/lines/groups/derived flags — a list never N+1s."""
        if not invoices:
            return
        ids = [i.id for i in invoices]
        names = await self._company_names(invoices)
        lines_by_doc = await self._doc_lines(ids)
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
            totals = _totals_from_rows(rows, prices_include_tax=invoice.prices_include_tax)
            invoice.company_name = names.get(invoice.company_id, "")  # type: ignore[attr-defined]
            invoice.lines = rows  # type: ignore[attr-defined]
            invoice.tax_groups = [  # type: ignore[attr-defined]
                {
                    "rate_pct": g.rate_pct, "category": g.category, "name": g.name,
                    "base": g.base, "tax": g.tax,
                }
                for g in totals.groups
            ]
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
        contact_email = (
            await _contact_email(self.ctx, data.contact_id) if data.contact_id else None
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
        invoice = await self.repo.create(
            company_id=data.company_id,
            contact_id=data.contact_id,
            kind=data.kind.value,
            customer=_customer_snapshot(company, email=contact_email),
            currency=(data.currency or currency).upper(),
            exchange_rate=data.exchange_rate,
            locale=doc_locale,
            reference=data.reference,
            intro=data.intro,
            notes=data.notes,
            template_id=data.template_id or settings_row.default_template_id,
            issue_date=data.issue_date,
            due_date=data.due_date,
            prices_include_tax=include_tax,
            custom=custom,
        )
        await self._replace_lines(invoice, line_rows)
        await ActivityService(self.ctx).record_created(self.entity_type, invoice.id)
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
            "reference", "intro", "notes", "issue_date", "due_date",
            "exchange_rate", "reminders_paused",
        ):
            if field in sent:
                values[field] = sent[field]
        if "locale" in sent and data.locale is not None:
            values["locale"] = data.locale
        if "currency" in sent and data.currency is not None:
            values["currency"] = data.currency
        if "prices_include_tax" in sent and data.prices_include_tax is not None:
            values["prices_include_tax"] = data.prices_include_tax
        if "contact_id" in sent:
            if data.contact_id is not None:
                email = await _contact_email(self.ctx, data.contact_id)
                customer = dict(invoice.customer)
                customer["email"] = email or customer.get("email")
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
            )
            await self._replace_lines(invoice, line_rows)

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
        email = invoice.customer.get("email") if invoice.customer else None
        number = await self.settings.allocate_number("invoice")
        invoice = await self.repo.update(
            invoice,
            number=number,
            status=InvoiceStatus.OPEN.value,
            issue_date=issue_date,
            due_date=due_date,
            customer=_customer_snapshot(company, email=email),
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
            from app.modules.invoicing import emails

            message = emails.compose_invoice_email(
                invoice, self.ctx.org.name, data.message
            )
            message.to = to
            await emails.deliver(self.ctx, message)
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
        from app.modules.invoicing import emails

        message = emails.compose_reminder_email(invoice, self.ctx.org.name, max(days, 0))
        message.to = to
        await emails.deliver(self.ctx, message)
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
    ) -> dict[str, Any]:
        """Approved + billable + not-yet-invoiced entries for a company — what the
        "Uren factureren" dialog shows before it builds the draft."""
        self.ctx.require("invoicing.invoice.write")
        await _company_row(self.ctx, company_id)
        rows = await self._unbilled_rows(company_id, project_id=project_id, until=until)
        settings_row = await self.settings.row()
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
                }
                for row in rows
            ],
            "total_minutes": sum(row["minutes"] for row in rows),
            "hourly_rate": settings_row.default_hourly_rate,
        }

    async def _unbilled_rows(
        self,
        company_id: uuid.UUID,
        *,
        project_id: uuid.UUID | None,
        until: date | None,
    ) -> list[Any]:
        # Bare-table reads over published columns (§6): the time module's "to invoice" set is
        # approved AND billable AND not invoiced (time/models.py), joined for display names.
        clauses = """
            te.org_id = :oid AND te.company_id = :cid AND te.ended_at IS NOT NULL
            AND te.billable AND te.approved_at IS NOT NULL AND te.invoiced_at IS NULL
            AND te.minutes > 0
        """
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
        stmt = text(
            f"""
            SELECT te.id, te.started_at, te.minutes, te.description, te.project_id,
                   p.name AS project_name, p.hourly_rate AS project_rate,
                   u.full_name AS user_name
            FROM time_entries te
            LEFT JOIN projects p ON p.id = te.project_id AND p.org_id = te.org_id
            LEFT JOIN users u ON u.id = te.user_id
            WHERE {clauses}
            ORDER BY te.started_at
            """  # noqa: S608 - static column clauses, bound params only
        )
        return list((await self.ctx.session.execute(stmt, params)).mappings().all())

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
            if data.hourly_rate is not None:
                return data.hourly_rate
            if row["project_rate"] is not None:
                return Decimal(row["project_rate"])
            return Decimal(fallback_rate)

        def hours(minutes: int) -> Decimal:
            return (Decimal(minutes) / Decimal(60)).quantize(Decimal("0.01"))

        lines: list[LineWrite] = []
        if data.group_by == "project":
            groups: dict[Any, dict[str, Any]] = {}
            for row in rows:
                bucket = groups.setdefault(
                    row["project_id"],
                    {"name": row["project_name"], "minutes": 0, "rate": rate_for(row)},
                )
                bucket["minutes"] += row["minutes"]
            for bucket in groups.values():
                lines.append(
                    LineWrite(
                        description=bucket["name"] or "Uren",
                        quantity=hours(bucket["minutes"]),
                        unit="uur",
                        unit_price=bucket["rate"],
                    )
                )
        elif data.group_by == "day":
            by_day: dict[tuple[Any, Any], dict[str, Any]] = {}
            for row in rows:
                day = row["started_at"].date()
                bucket = by_day.setdefault(
                    (day, row["project_id"]),
                    {
                        "day": day, "name": row["project_name"], "minutes": 0,
                        "rate": rate_for(row),
                    },
                )
                bucket["minutes"] += row["minutes"]
            for bucket in by_day.values():
                label = bucket["day"].strftime("%d-%m-%Y")
                name = f"{bucket['name']} · {label}" if bucket["name"] else label
                lines.append(
                    LineWrite(
                        description=name,
                        quantity=hours(bucket["minutes"]),
                        unit="uur",
                        unit_price=bucket["rate"],
                    )
                )
        else:  # entry
            for row in rows:
                label = row["started_at"].date().strftime("%d-%m-%Y")
                description = row["description"] or row["project_name"] or "Uren"
                lines.append(
                    LineWrite(
                        description=f"{label} · {description}"[:512],
                        quantity=hours(row["minutes"]),
                        unit="uur",
                        unit_price=rate_for(row),
                    )
                )

        invoice = await self.create(
            InvoiceCreate(company_id=data.company_id, lines=lines)
        )
        entry_ids = [row["id"] for row in rows]
        for entry_id in entry_ids:
            await self.ctx.repo(InvoiceTimeEntry).create(
                invoice_id=invoice.id, time_entry_id=entry_id
            )
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
        return invoice

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
        stored rate; 1 when unset). Approximate for steering — documents stay exact."""
        today = await org_today(self.ctx)
        base = "COALESCE(exchange_rate, 1)"
        row = (
            await self.ctx.session.execute(
                text(
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
                    FROM invoices WHERE org_id = :oid
                    """  # noqa: S608 - f-string only splices the constant COALESCE expr
                ),
                {"oid": self.ctx.org.id, "today": today, "year": today.year},
            )
        ).mappings().one()
        quotes = (
            await self.ctx.session.execute(
                text(
                    f"""
                    SELECT COUNT(*) AS open_count,
                           COALESCE(SUM(total * {base}), 0) AS open_total
                    FROM quotes WHERE org_id = :oid AND status = 'open'
                    """  # noqa: S608
                ),
                {"oid": self.ctx.org.id},
            )
        ).mappings().one()
        return {
            "open_count": row["open_count"],
            "open_total": round(float(row["open_total"]), 2),
            "overdue_count": row["overdue_count"],
            "overdue_total": round(float(row["overdue_total"]), 2),
            "draft_count": row["draft_count"],
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
                select(func.count())
                .select_from(Quote)
                .where(Quote.org_id == self.ctx.org.id, *conditions)
            )
            or 0
        )
        await self._attach(items)
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
        await self._attach(items)
        return items

    async def _attach(self, quotes: Sequence[Quote]) -> None:
        if not quotes:
            return
        names = await self._company_names(quotes)
        lines_by_doc = await self._doc_lines([q.id for q in quotes])
        today = await org_today(self.ctx)
        for quote in quotes:
            rows = lines_by_doc.get(quote.id, [])
            totals = _totals_from_rows(rows, prices_include_tax=quote.prices_include_tax)
            quote.company_name = names.get(quote.company_id, "")  # type: ignore[attr-defined]
            quote.lines = rows  # type: ignore[attr-defined]
            quote.tax_groups = [  # type: ignore[attr-defined]
                {
                    "rate_pct": g.rate_pct, "category": g.category, "name": g.name,
                    "base": g.base, "tax": g.tax,
                }
                for g in totals.groups
            ]
            quote.expired = (  # type: ignore[attr-defined]
                quote.status in (QuoteStatus.OPEN.value, QuoteStatus.EXPIRED.value)
                and quote.valid_until is not None
                and quote.valid_until < today
            ) or quote.status == QuoteStatus.EXPIRED.value

    async def create(self, data: QuoteCreate) -> Quote:
        self.ctx.require("invoicing.quote.write")
        company = await _company_row(self.ctx, data.company_id)
        contact_email = (
            await _contact_email(self.ctx, data.contact_id) if data.contact_id else None
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
            customer=_customer_snapshot(company, email=contact_email),
            currency=(data.currency or currency).upper(),
            exchange_rate=data.exchange_rate,
            locale=doc_locale,
            reference=data.reference,
            intro=data.intro,
            notes=data.notes,
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
        if "locale" in sent and data.locale is not None:
            values["locale"] = data.locale
        if "currency" in sent and data.currency is not None:
            values["currency"] = data.currency
        if "prices_include_tax" in sent and data.prices_include_tax is not None:
            values["prices_include_tax"] = data.prices_include_tax
        if "contact_id" in sent:
            if data.contact_id is not None:
                email = await _contact_email(self.ctx, data.contact_id)
                customer = dict(quote.customer)
                customer["email"] = email or customer.get("email")
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
        email = quote.customer.get("email") if quote.customer else None
        number = await self.settings.allocate_number("quote")
        quote = await self.repo.update(
            quote,
            number=number,
            status=QuoteStatus.OPEN.value,
            issue_date=issue_date,
            valid_until=valid_until,
            customer=_customer_snapshot(company, email=email),
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
            from app.modules.invoicing import emails

            message = emails.compose_quote_email(quote, self.ctx.org.name, data.message)
            message.to = to
            await emails.deliver(self.ctx, message)
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
