"""Spreadsheet shape for invoices — the back catalogue comes in here (docs/INVOICING.md).

Core owns the mechanics (``app/core/impex``); this file describes the shape and adapts the
coerced row to the module's own service. One row is one invoice **with its totals**: a sheet
from Moneybird, SnelStart, e-Boekhouden or a spreadsheet carries number, client, dates,
subtotal/tax/total, a status and how much has been paid — and rarely a line. An imported
document's totals are the fact (``service.import_document``), so no line vocabulary is asked
for; the one summary line the document prints takes its text from ``description``.

Upsert on ``number``. A **new** number is an invoice issued elsewhere and is created as
``origin=imported``; an **existing** one — native or imported — may have its process fields and
its payment *state* brought up to date, and never its money (``service.plan_import_update``).
That second half is what makes "mark these forty paid from the bank statement" one file.

Every rule the preview must be able to name runs through ``validate_row`` (#289): the same two
functions the service calls, so the dry run can never pass a row the write then refuses.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from sqlalchemy import column, select, table

from app.core.impex import ImpexColumn, ImpexDescriptor
from app.core.impex.resolvers import name_or_id_resolver
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.invoicing.calc import outstanding_of
from app.modules.invoicing.models import Invoice, InvoiceKind, InvoiceOrigin, InvoiceStatus
from app.modules.invoicing.schemas import InvoiceImport
from app.modules.invoicing.service import (
    InvoiceService,
    plan_import,
    plan_import_update,
)

#: What a sheet may state. ``draft`` is deliberately absent: a document with a number is not a
#: draft, and a draft has no number to match on.
_STATUSES = (InvoiceStatus.OPEN.value, InvoiceStatus.PAID.value, InvoiceStatus.CANCELLED.value)
_KINDS = (InvoiceKind.INVOICE.value, InvoiceKind.CREDIT_NOTE.value)
_METHODS = ("bank", "cash", "card", "other")


async def _fetch_page(
    ctx: RequestContext, *, limit: int, offset: int, filters: dict[str, Any]
) -> Sequence[Any]:
    """The module's own list — same filters and sort as ``GET /invoicing/invoices``, without
    the lines (#290): an export row prints totals, never a line."""
    service = InvoiceService(ctx)
    items, _ = await service.list(
        limit=limit,
        offset=offset,
        status=filters.get("status"),
        company_id=filters.get("company_id"),
        q=filters.get("q"),
        sort=filters.get("sort"),
        lines=False,
    )
    # A credit note names its source by number in the file (``credit_for``), which the list
    # read does not carry (it is a detail-read field). One grouped query for the page.
    sources = {i.credit_for_id for i in items if i.credit_for_id}
    numbers: dict[uuid.UUID, str] = {}
    if sources:
        rows = await ctx.session.execute(
            service.repo.scoped_select().where(Invoice.id.in_(list(sources)))
        )
        numbers = {row.id: row.number or "" for row in rows.scalars()}
    for invoice in items:
        invoice.credit_for_number = (  # type: ignore[attr-defined]
            numbers.get(invoice.credit_for_id, "") if invoice.credit_for_id else ""
        )
    return items


async def _find_existing(
    ctx: RequestContext, key: str, values: list[str]
) -> dict[str, list[Any]]:
    """Issued documents by number. Numbers are org-unique at the database level, so a bucket
    holds at most one row and the match can never be ambiguous."""
    stmt = InvoiceService(ctx).repo.scoped_select().where(Invoice.number.in_(values))
    found: dict[str, list[Any]] = {}
    for invoice in (await ctx.session.execute(stmt)).scalars():
        found.setdefault(invoice.number, []).append(invoice)
    return found


async def _resolve_invoice_numbers(
    ctx: RequestContext, refs: list[str]
) -> dict[str, uuid.UUID | str]:
    """``credit_for``: the invoice a credit note corrects, by its number (or id)."""
    by_id: dict[str, uuid.UUID] = {}
    numbers: list[str] = []
    for ref in refs:
        try:
            by_id[ref] = uuid.UUID(ref)
        except ValueError:
            numbers.append(ref)
    invoices = table("invoices", column("id"), column("number"), column("org_id"))
    resolved: dict[str, uuid.UUID | str] = {}
    if by_id:
        found = set(
            (
                await ctx.session.execute(
                    select(invoices.c.id).where(
                        invoices.c.org_id == ctx.org.id, invoices.c.id.in_(by_id.values())
                    )
                )
            ).scalars()
        )
        for ref, ref_id in by_id.items():
            resolved[ref] = ref_id if ref_id in found else "impex.errors.unresolved_reference"
    if numbers:
        rows = await ctx.session.execute(
            select(invoices.c.id, invoices.c.number).where(
                invoices.c.org_id == ctx.org.id, invoices.c.number.in_(numbers)
            )
        )
        by_number = {number: row_id for row_id, number in rows}
        for number in numbers:
            resolved[number] = by_number.get(number, "impex.errors.unresolved_reference")
    return resolved


def _to_import(values: dict[str, Any]) -> InvoiceImport:
    """The coerced row as the service's shape. ``None`` for an absent cell throughout —
    the schema's defaults decide what absence means."""
    fields = {
        key: values[key]
        for key in (
            "number", "company_id", "issue_date", "due_date", "delivery_date", "reference",
            "currency", "locale", "subtotal", "tax_total", "total", "paid_total", "paid_on",
            "sent_on", "description", "import_source", "credit_for_id", "notes",
        )
        if values.get(key) not in (None, "")
    }
    if values.get("kind"):
        fields["kind"] = InvoiceKind(values["kind"])
    if values.get("status"):
        fields["status"] = InvoiceStatus(values["status"])
    if values.get("payment_method"):
        fields["payment_method"] = values["payment_method"]
    if isinstance(values.get("reminders"), bool):
        fields["reminders"] = values["reminders"]
    fields["custom"] = values.get("custom") or {}
    return InvoiceImport(**fields)


async def _validate_row(
    ctx: RequestContext, values: dict[str, Any], existing: Any | None
) -> Sequence[tuple[str, str]]:
    """The service's own rules, run in the preview so the report names the row (#289)."""
    try:
        data = _to_import(values)
    except ValueError:
        # Pydantic refused the shape (a bad currency code, a too-long reference); the engine's
        # own column coercions have already named what they could, so this is the remainder.
        return [("number", "errors.validation")]
    try:
        if existing is None:
            plan_import(data)
            if data.credit_for_id is not None:
                await InvoiceService(ctx)._import_credit_source(data.credit_for_id)  # noqa: SLF001
        else:
            plan_import_update(existing, data)
    except AppError as error:
        return list((error.fields or {"number": error.message_key}).items())
    return []


async def _create(ctx: RequestContext, values: dict[str, Any]) -> Any:
    return await InvoiceService(ctx).import_document(_to_import(values))


async def _update(ctx: RequestContext, invoice: Any, values: dict[str, Any]) -> None:
    await InvoiceService(ctx).import_update(invoice, _to_import(values))


def _money(value: Any) -> str:
    return str(Decimal(value).quantize(Decimal("0.01")))


def _company_ref(invoice: Any) -> str:
    """The client as the export names it: its client number where it has one — the key the
    bookkeeper's own files use — else its name. Both resolve on the way back in."""
    customer = invoice.customer or {}
    return customer.get("client_number") or getattr(invoice, "company_name", "") or ""


INVOICE_IMPEX = ImpexDescriptor(
    entity_type="invoice",
    read_permission="invoicing.invoice.read",
    write_permission="invoicing.invoice.write",
    natural_keys=("number",),
    filters=("q", "status", "company_id", "sort"),
    columns=(
        ImpexColumn(
            "number", required=True, clearable=False,
            aliases=("factuurnummer", "nummer", "invoice number", "invoice no", "factuur"),
        ),
        ImpexColumn(
            "kind", data_type="select", options=_KINDS, clearable=False,
            option_label_key="invoicing.kind.{option}",
            aliases=("soort", "type", "document type"),
        ),
        # The client, by client number, name or legal name (``name_or_id_resolver``). Not
        # clearable: an invoice with no client is nonsense.
        ImpexColumn(
            "company", data_type="fk", field="company_id", required=True, clearable=False,
            getter=_company_ref,
            aliases=("klant", "bedrijf", "client", "customer", "debiteur", "relatie",
                     "klantnummer", "client number"),
        ),
        ImpexColumn(
            "issue_date", data_type="date", required=True, clearable=False,
            aliases=("factuurdatum", "datum", "date", "invoice date"),
        ),
        ImpexColumn(
            "due_date", data_type="date", clearable=False,
            aliases=("vervaldatum", "vervalt op", "due", "due on"),
        ),
        ImpexColumn(
            "delivery_date", data_type="date", clearable=False,
            aliases=("leverdatum", "delivered on"),
        ),
        ImpexColumn("reference", aliases=("referentie", "kenmerk", "po", "your reference")),
        ImpexColumn("currency", clearable=False, aliases=("valuta", "munt")),
        ImpexColumn(
            "subtotal", data_type="number", clearable=False, getter=lambda i: _money(i.subtotal),
            aliases=("subtotaal", "excl", "excl. btw", "netto", "net", "bedrag excl"),
        ),
        ImpexColumn(
            "tax_total", data_type="number", clearable=False, getter=lambda i: _money(i.tax_total),
            aliases=("btw", "btw bedrag", "vat", "tax", "belasting"),
        ),
        ImpexColumn(
            "total", data_type="number", required=True, clearable=False,
            getter=lambda i: _money(i.total),
            aliases=("totaal", "incl", "incl. btw", "bruto", "gross", "bedrag", "amount"),
        ),
        ImpexColumn(
            "status", data_type="select", options=_STATUSES, clearable=False,
            option_label_key="invoicing.status.{option}",
            aliases=("betaalstatus", "state"),
        ),
        # The payment **state**: how much has been received, and when. On an existing
        # invoice a higher figure registers the difference; a lower one is refused.
        ImpexColumn(
            "paid_total", data_type="number", clearable=False,
            getter=lambda i: _money(abs(i.paid_total)),
            aliases=("betaald", "ontvangen", "paid", "received", "betaald bedrag"),
        ),
        ImpexColumn(
            "paid_on", data_type="date", clearable=False,
            getter=lambda i: _last_paid_on(i),
            aliases=("betaaldatum", "betaald op", "paid on", "payment date"),
        ),
        ImpexColumn(
            "payment_method", data_type="select", options=_METHODS, clearable=False,
            option_label_key="invoicing.payment.method.{option}",
            getter=lambda i: _last_method(i),
            aliases=("betaalwijze", "betaalmethode", "method"),
        ),
        ImpexColumn(
            "sent_on", data_type="date", clearable=False,
            getter=lambda i: i.sent_at.date() if i.sent_at else None,
            aliases=("verzonden", "verzonden op", "sent", "sent on"),
        ),
        # Dunning is opt-in for the back catalogue: mail from a new system about old invoices
        # is a decision. Exported as the invoice's actual setting so a round trip keeps it.
        ImpexColumn(
            "reminders", data_type="bool", clearable=False,
            getter=lambda i: not i.reminders_paused,
            aliases=("herinneringen", "aanmanen", "reminders"),
        ),
        # Import-only: what the one summary line says. Exported empty on purpose — a native
        # invoice has real lines and this column must not pretend to be one of them.
        ImpexColumn(
            "description", getter=lambda i: None,
            aliases=("omschrijving", "line", "regel"),
        ),
        ImpexColumn(
            "import_source",
            aliases=("bron", "herkomst", "source", "system"),
        ),
        # A credit note's source, by number. The resolver hands back the id; the service checks
        # that it is an issued invoice and not itself a note.
        ImpexColumn(
            "credit_for", data_type="fk", field="credit_for_id", clearable=False,
            getter=lambda i: getattr(i, "credit_for_number", "") or None,
            aliases=("creditering van", "credit for", "corrigeert"),
        ),
        ImpexColumn("notes", aliases=("notities", "opmerkingen", "note")),
        # Derived, export-only: what the register knows that a sheet cannot set.
        ImpexColumn("outstanding", readonly=True, getter=lambda i: _money(outstanding_of(i))),
        ImpexColumn("origin", readonly=True),
        ImpexColumn(
            "original", data_type="bool", readonly=True,
            getter=lambda i: i.original_file_id is not None,
        ),
    ),
    fetch_page=_fetch_page,
    find_existing=_find_existing,
    create_row=_create,
    update_row=_update,
    fk_resolvers={
        "company": name_or_id_resolver("companies"),
        "credit_for": _resolve_invoice_numbers,
    },
    validate_row=_validate_row,
)


def _last_paid_on(invoice: Any) -> Any:
    """The export's ``paid_on``: the latest registered payment's date, so a round trip of a
    paid register re-imports as paid on the day it was — and a list read (no payments
    attached) falls back to ``paid_at``'s day, which ``_settle`` stamps for a native invoice
    and the import stamps from the sheet."""
    payments = getattr(invoice, "payments", None)
    if payments:
        return max(payment.paid_on for payment in payments)
    if invoice.paid_at is not None:
        return invoice.paid_at.date()
    return None


def _last_method(invoice: Any) -> str | None:
    payments = getattr(invoice, "payments", None)
    if payments:
        return payments[-1].method
    return None


#: The one thing the export must state about itself: an *imported* row carries its origin, so
#: a file taken out of one instance says which of its rows were somebody else's documents.
_ = InvoiceOrigin
