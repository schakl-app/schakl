"""One-time product sales — a product sold once to a client, waiting to be invoiced.

The price list (``Product``) says what the agency sells; a sale says that it sold one of them
to *this* client, on *this* day, possibly for *this* project, and whether a document has billed
it yet. It is a subscription with no cycle: the same three surfaces an agreement period reaches
— the client's ``outstanding`` picker, the org-wide backlog, and the line that finally bills it
— and none of the machinery an interval needs.

The invoicing service owns the *claim* (``InvoiceService._reconcile_sales``): a line carrying
``sale_id`` sets ``invoice_id`` on the sale, a save that drops the line clears it, and a delete,
cancel or full credit of the document releases it. This service owns the record — creating it
from a product (snapshotting the price, the tax-rate discipline), editing it while it is still
open, and ``invoice()``, which drafts one document with one line through the ordinary create
so the claim is made by the same code every hand-built invoice goes through.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import bindparam, false, func, text

from app.core.activity import ActivityService
from app.core.activity.service import snapshot
from app.core.tenancy import RequestContext, TenantScopedRepository
from app.errors import AppError
from app.modules.invoicing.calc import line_amount, round_cents
from app.modules.invoicing.models import (
    Invoice,
    InvoiceKind,
    LineKind,
    Product,
    ProductSale,
    TaxRate,
)
from app.modules.invoicing.schemas import (
    InvoiceCreate,
    LineWrite,
    ProductSaleCreate,
    ProductSaleUpdate,
)

ENTITY_SALE = "product_sale"

#: What an edit may still change once a document bills the sale: the note and the project
#: it is filed under — never its money, which the line has already snapshotted, and never
#: the day, which is what the backlog sorted it on when it was picked.
_EDITABLE_WHEN_INVOICED = frozenset({"notes", "project_id"})

_AUDITED_FIELDS = (
    "name",
    "description",
    "quantity",
    "unit",
    "unit_price",
    "tax_rate_id",
    "sold_on",
    "project_id",
    "notes",
)


class ProductSaleService:
    class _PortalRepository(TenantScopedRepository):
        """A client reads no sale at all (``ProductSale.__portal_horizon_clause__``)."""

        def horizon_condition(self):  # noqa: ANN202 — mirrors the base signature
            return false()

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = (
            self._PortalRepository(
                ctx.session, ctx.org.id, ProductSale, company_scope=ctx.company_scope
            )
            if ctx.is_portal
            else ctx.repo(ProductSale)
        )

    # --- reads --------------------------------------------------------------- #
    async def list(
        self,
        *,
        limit: int,
        offset: int,
        company_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
        product_id: uuid.UUID | None = None,
        status: str = "all",
        q: str | None = None,
    ) -> dict[str, Any]:
        conditions = []
        if company_id is not None:
            conditions.append(ProductSale.company_id == company_id)
        if project_id is not None:
            conditions.append(ProductSale.project_id == project_id)
        if product_id is not None:
            conditions.append(ProductSale.product_id == product_id)
        if status == "open":
            conditions.append(ProductSale.invoice_id.is_(None))
        elif status == "invoiced":
            conditions.append(ProductSale.invoice_id.is_not(None))
        if q:
            needle = f"%{q.strip()}%"
            conditions.append(
                ProductSale.name.ilike(needle) | ProductSale.description.ilike(needle)
            )
        stmt = (
            self.repo.scoped_select()
            .where(*conditions)
            # Newest sale first; what is still open is what the panel is opened for, so a
            # secondary sort keeps open rows ahead of invoiced ones on the same day.
            .order_by(
                ProductSale.sold_on.desc(),
                ProductSale.invoice_id.is_not(None),
                ProductSale.created_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        # The total and the open figure are over the **whole** filtered set, not the page — a
        # panel prints the open amount beside its heading, and a number computed from a capped
        # list is the truncated total docs/PERFORMANCE.md forbids. One statement for the three:
        # the hub composes this panel beside a dozen others under one query budget.
        is_open = ProductSale.invoice_id.is_(None)
        totals = (
            await self.ctx.session.execute(
                self.repo.scoped_select()
                .with_only_columns(
                    func.count(ProductSale.id),
                    func.count(ProductSale.id).filter(is_open),
                    func.coalesce(
                        func.sum(ProductSale.quantity * ProductSale.unit_price).filter(is_open),
                        0,
                    ),
                )
                .where(*conditions)
                .order_by(None)
            )
        ).one()
        return {
            "items": await self.serialise(items),
            "total": int(totals[0] or 0),
            "open_count": int(totals[1] or 0),
            "open_amount": round_cents(Decimal(str(totals[2] or 0))),
        }

    async def get(self, sale_id: uuid.UUID) -> dict[str, Any]:
        sale = await self.repo.get_or_404(sale_id)
        return (await self.serialise([sale]))[0]

    async def open_sales(self, company_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
        """Every sale not on a document yet — one client's for the picker, org-wide for the
        backlog. One read plus the name lookups, whatever the count."""
        stmt = self.repo.scoped_select().where(ProductSale.invoice_id.is_(None))
        if company_id is not None:
            stmt = stmt.where(ProductSale.company_id == company_id)
        stmt = stmt.order_by(ProductSale.sold_on, ProductSale.created_at)
        items = list((await self.ctx.session.execute(stmt)).scalars().all())
        return await self.serialise(items)

    async def serialise(self, sales: Sequence[ProductSale]) -> list[dict[str, Any]]:
        """Rows with their client, project and document named — three batched lookups for
        the whole list, never one per row."""
        if not sales:
            return []
        company_ids = {s.company_id for s in sales}
        project_ids = {s.project_id for s in sales if s.project_id}
        invoice_ids = {s.invoice_id for s in sales if s.invoice_id}
        companies = await self._names("companies", company_ids)
        projects = await self._names("projects", project_ids)
        invoices: dict[uuid.UUID, Invoice] = {}
        if invoice_ids:
            invoices = {
                inv.id: inv
                for inv in await self.ctx.session.scalars(
                    self.ctx.repo(Invoice).scoped_select().where(Invoice.id.in_(invoice_ids))
                )
            }
        out = []
        for sale in sales:
            invoice = invoices.get(sale.invoice_id) if sale.invoice_id else None
            out.append(
                {
                    "id": sale.id,
                    "org_id": sale.org_id,
                    "company_id": sale.company_id,
                    "company_name": companies.get(sale.company_id, ""),
                    "project_id": sale.project_id,
                    "project_name": projects.get(sale.project_id) if sale.project_id else None,
                    "product_id": sale.product_id,
                    "name": sale.name,
                    "description": sale.description,
                    "quantity": sale.quantity,
                    "unit": sale.unit,
                    "unit_price": sale.unit_price,
                    "tax_rate_id": sale.tax_rate_id,
                    "currency": sale.currency,
                    "sold_on": sale.sold_on,
                    "notes": sale.notes,
                    "amount": line_amount(sale.quantity, sale.unit_price),
                    "status": "invoiced" if sale.invoice_id else "open",
                    "invoice_id": sale.invoice_id,
                    "invoice_number": invoice.number if invoice else None,
                    "invoice_status": invoice.status if invoice else None,
                    "invoiced_at": sale.invoiced_at,
                    "created_at": sale.created_at,
                    "updated_at": sale.updated_at,
                }
            )
        return out

    async def _names(self, table: str, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
        if not ids:
            return {}
        rows = await self.ctx.session.execute(
            text(
                f"SELECT id, name FROM {table} WHERE org_id = :oid AND id IN :ids"  # noqa: S608 - table from a module literal, bound params
            ).bindparams(bindparam("ids", expanding=True)),
            {"oid": self.ctx.org.id, "ids": list(ids)},
        )
        return {row.id: row.name for row in rows}

    # --- writes -------------------------------------------------------------- #
    async def create(self, data: ProductSaleCreate) -> dict[str, Any]:
        self.ctx.require("invoicing.invoice.write")
        values = data.model_dump()
        product = None
        if values.get("product_id") is not None:
            product = await self.ctx.session.scalar(
                self.ctx.repo(Product)
                .scoped_select()
                .where(Product.id == values["product_id"])
            )
            if product is None:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"product_id": "errors.not_found"},
                )
        # The product fills whatever the sale left blank — and only at birth. What is copied
        # is a snapshot: a price list re-priced next year never rewrites what was sold today.
        if product is not None:
            values["name"] = values.get("name") or product.name
            if values.get("description") is None:
                values["description"] = product.description
            if values.get("unit") is None:
                values["unit"] = product.unit
            if values.get("unit_price") is None:
                values["unit_price"] = product.unit_price
            if values.get("tax_rate_id") is None:
                values["tax_rate_id"] = product.tax_rate_id
        if values.get("unit_price") is None:
            values["unit_price"] = Decimal(0)
        if values.get("sold_on") is None:
            values["sold_on"] = await self._today()
        await self._ensure_company(values["company_id"])
        await self._ensure_project(values.get("project_id"), values["company_id"])
        await self._ensure_tax_rate(values.get("tax_rate_id"))
        values["currency"] = await self._currency()
        sale = await self.repo.create(**values)
        await ActivityService(self.ctx).record_created(ENTITY_SALE, sale.id)
        return (await self.serialise([sale]))[0]

    async def update(self, sale_id: uuid.UUID, data: ProductSaleUpdate) -> dict[str, Any]:
        self.ctx.require("invoicing.invoice.write")
        sale = await self.repo.get_or_404(sale_id)
        values = data.model_dump(exclude_unset=True)
        if sale.invoice_id is not None:
            # The line has snapshotted the money and the document may already be on the
            # client's desk: re-pricing a billed sale would make the record disagree with the
            # paper. Named as a conflict, so the form can say which.
            frozen = sorted(set(values) - _EDITABLE_WHEN_INVOICED)
            if frozen:
                raise AppError(
                    "conflict",
                    "errors.invoicing.sale_invoiced",
                    status_code=409,
                    fields={field: "errors.invoicing.sale_invoiced" for field in frozen},
                )
        if "project_id" in values:
            await self._ensure_project(values["project_id"], sale.company_id)
        if "tax_rate_id" in values:
            await self._ensure_tax_rate(values["tax_rate_id"])
        before = snapshot(sale, _AUDITED_FIELDS)
        sale = await self.repo.update(sale, **values)
        await ActivityService(self.ctx).record_update(
            ENTITY_SALE, sale.id, before, snapshot(sale, _AUDITED_FIELDS)
        )
        return (await self.serialise([sale]))[0]

    async def delete(self, sale_id: uuid.UUID) -> None:
        self.ctx.require("invoicing.invoice.write")
        sale = await self.repo.get_or_404(sale_id)
        if sale.invoice_id is not None:
            # The document is the record now; deleting the sale from under it would leave a
            # line that says it billed something no longer there. Remove the line first.
            raise AppError("conflict", "errors.invoicing.sale_invoiced", status_code=409)
        await ActivityService(self.ctx).record(
            ENTITY_SALE,
            sale.id,
            "deleted",
            {"name": sale.name, "amount": float(line_amount(sale.quantity, sale.unit_price))},
        )
        await self.repo.delete(sale)

    async def invoice(self, sale_id: uuid.UUID) -> Invoice:
        """Draft one invoice billing exactly this sale.

        Through ``InvoiceService.create`` and a line carrying ``sale_id``, never by writing an
        invoice row here: the create is what snapshots the tax label in the document's locale,
        applies the org's defaults, writes the trail — and makes the claim, through the same
        reconcile a hand-built document goes through. One code path for "this sale is on that
        document", whoever put it there.
        """
        from app.modules.invoicing.service import InvoiceService

        self.ctx.require("invoicing.invoice.write")
        sale = await self.repo.get_or_404(sale_id)
        if sale.invoice_id is not None:
            raise AppError("conflict", "errors.invoicing.sale_invoiced", status_code=409)
        invoice = await InvoiceService(self.ctx).create(
            InvoiceCreate(
                company_id=sale.company_id,
                kind=InvoiceKind.INVOICE,
                currency=sale.currency,
                lines=[
                    LineWrite(
                        description=(sale.description or sale.name)[:512],
                        line_kind=LineKind.PRODUCT,
                        quantity=sale.quantity,
                        unit=sale.unit,
                        unit_price=sale.unit_price,
                        tax_rate_id=sale.tax_rate_id,
                        sale_id=sale.id,
                    )
                ],
            )
        )
        await self.ctx.session.refresh(sale)
        if sale.invoice_id != invoice.id:
            # The reconcile skipped it — a horizon or a race. Refuse rather than hand back a
            # document that does not bill what was asked (bill less, never guess).
            raise AppError("conflict", "errors.invoicing.sale_invoiced", status_code=409)
        return invoice

    # --- guards -------------------------------------------------------------- #
    async def _ensure_company(self, company_id: uuid.UUID) -> None:
        row = await self.ctx.session.scalar(
            text("SELECT id FROM companies WHERE id = :cid AND org_id = :oid"),
            {"cid": company_id, "oid": self.ctx.org.id},
        )
        if row is None:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"company_id": "errors.not_found"},
            )

    async def _ensure_project(self, project_id: uuid.UUID | None, company_id: uuid.UUID) -> None:
        """A project is this client's or it is not this sale's (§6: through the table, never
        the module)."""
        if project_id is None:
            return
        row = (
            await self.ctx.session.execute(
                text(
                    "SELECT company_id FROM projects WHERE id = :pid AND org_id = :oid"
                ),
                {"pid": project_id, "oid": self.ctx.org.id},
            )
        ).first()
        if row is None or (row[0] is not None and row[0] != company_id):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"project_id": "errors.invoicing.sale_project_other_client"},
            )

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

    async def _today(self) -> date:
        from app.modules.invoicing.service import org_today

        return await org_today(self.ctx)

    async def _currency(self) -> str:
        from app.modules.invoicing.service import _org_defaults

        currency, _locale = await _org_defaults(self.ctx)
        return (currency or "EUR").upper()
