"""REST endpoints for invoicing under ``/api/v1/invoicing`` (issue #207).

Every route declares its permission (deny-by-default, §15). Static segments are declared
before ``/{invoice_id}`` so "settings"/"summary" never match an id path param.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.invoicing import accounting
from app.modules.invoicing.models import InvoiceStatus
from app.modules.invoicing.schemas import (
    DocumentSend,
    ExternalRefRead,
    InvoiceCreate,
    InvoiceFromTime,
    InvoiceIssue,
    InvoiceRead,
    InvoiceUpdate,
    InvoicingSettingsRead,
    InvoicingSettingsWrite,
    InvoicingSummary,
    PaymentWrite,
    QuoteCreate,
    QuoteDecision,
    QuoteRead,
    QuoteUpdate,
    TaxRateCreate,
    TaxRateRead,
    TaxRateUpdate,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
    UnbilledRead,
)
from app.modules.invoicing.service import (
    ExternalRefService,
    InvoiceService,
    InvoicingSettingsService,
    QuoteService,
    TaxRateService,
    TemplateService,
    _totals_from_rows,
)
from app.modules.invoicing.ubl import invoice_ubl
from app.schemas import Page

router = APIRouter(prefix="/invoicing", tags=["invoicing"])


# --- settings ----------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=InvoicingSettingsRead,
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def get_settings(ctx: RequestContext = Depends(require_context)) -> InvoicingSettingsRead:
    """Read by the editor too (defaults, numbering preview) — not only by admins."""
    row = await InvoicingSettingsService(ctx).row()
    return InvoicingSettingsRead.model_validate(row)


@router.put(
    "/settings",
    response_model=InvoicingSettingsRead,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def save_settings(
    payload: InvoicingSettingsWrite,
    ctx: RequestContext = Depends(require_context),
) -> InvoicingSettingsRead:
    row = await InvoicingSettingsService(ctx).save(payload)
    return InvoicingSettingsRead.model_validate(row)


# --- tax rates ------------------------------------------------------------------ #
@router.get(
    "/tax-rates",
    response_model=list[TaxRateRead],
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def list_tax_rates(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[TaxRateRead]:
    items = await TaxRateService(ctx).list(include_inactive=include_inactive)
    return [TaxRateRead.model_validate(t) for t in items]


@router.post(
    "/tax-rates",
    response_model=TaxRateRead,
    status_code=201,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def create_tax_rate(
    payload: TaxRateCreate,
    ctx: RequestContext = Depends(require_context),
) -> TaxRateRead:
    return TaxRateRead.model_validate(await TaxRateService(ctx).create(payload))


@router.patch(
    "/tax-rates/{tax_rate_id}",
    response_model=TaxRateRead,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def update_tax_rate(
    tax_rate_id: uuid.UUID,
    payload: TaxRateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TaxRateRead:
    return TaxRateRead.model_validate(await TaxRateService(ctx).update(tax_rate_id, payload))


@router.delete(
    "/tax-rates/{tax_rate_id}",
    status_code=204,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def delete_tax_rate(
    tax_rate_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TaxRateService(ctx).delete(tax_rate_id)


# --- templates ------------------------------------------------------------------ #
@router.get(
    "/templates",
    response_model=list[TemplateRead],
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def list_templates(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[TemplateRead]:
    items = await TemplateService(ctx).list(include_inactive=include_inactive)
    return [TemplateRead.model_validate(t) for t in items]


@router.post(
    "/templates",
    response_model=TemplateRead,
    status_code=201,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def create_template(
    payload: TemplateCreate,
    ctx: RequestContext = Depends(require_context),
) -> TemplateRead:
    return TemplateRead.model_validate(await TemplateService(ctx).create(payload))


@router.patch(
    "/templates/{template_id}",
    response_model=TemplateRead,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def update_template(
    template_id: uuid.UUID,
    payload: TemplateUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TemplateRead:
    return TemplateRead.model_validate(await TemplateService(ctx).update(template_id, payload))


@router.delete(
    "/templates/{template_id}",
    status_code=204,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def delete_template(
    template_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await TemplateService(ctx).delete(template_id)


# --- accounting seam -------------------------------------------------------------- #
@router.get(
    "/providers",
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def list_providers(ctx: RequestContext = Depends(require_context)) -> list[dict]:
    """The registered accounting adapters (#31). UBL export is always available and is not
    a provider — it's a download, listed by the web from its own knowledge."""
    return [{"key": p.key, "label": p.label} for p in accounting.available_providers()]


# --- summary ---------------------------------------------------------------------- #
@router.get(
    "/summary",
    response_model=InvoicingSummary,
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def summary(ctx: RequestContext = Depends(require_context)) -> InvoicingSummary:
    return InvoicingSummary.model_validate(await InvoiceService(ctx).summary())


# --- time bridge -------------------------------------------------------------------- #
@router.get(
    "/unbilled",
    response_model=UnbilledRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def unbilled(
    company_id: uuid.UUID = Query(...),
    project_id: uuid.UUID | None = Query(None),
    until: str | None = Query(None, description="org-local date (YYYY-MM-DD), inclusive"),
    ctx: RequestContext = Depends(require_context),
) -> UnbilledRead:
    from datetime import date as date_type

    parsed = date_type.fromisoformat(until) if until else None
    data = await InvoiceService(ctx).unbilled(company_id, project_id=project_id, until=parsed)
    return UnbilledRead.model_validate(data)


# --- invoices ------------------------------------------------------------------------ #
@router.get(
    "/invoices",
    response_model=Page[InvoiceRead],
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def list_invoices(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(None, description="draft | open | paid | cancelled"),
    company_id: uuid.UUID | None = Query(None),
    kind: str | None = Query(None, description="invoice | credit_note"),
    overdue: bool = Query(False, description="only open invoices past their due date"),
    q: str | None = Query(None, description="matches number and reference"),
    sort: str | None = Query(
        None, description="number | status | issue_date | due_date | total | created_at"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[InvoiceRead]:
    items, total = await InvoiceService(ctx).list(
        limit=limit, offset=offset, status=status, company_id=company_id,
        kind=kind, overdue=overdue, q=q, sort=sort,
    )
    return Page(
        items=[InvoiceRead.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.post(
    "/invoices",
    response_model=InvoiceRead,
    status_code=201,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def create_invoice(
    payload: InvoiceCreate,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(await InvoiceService(ctx).create(payload))


@router.post(
    "/invoices/from-time",
    response_model=InvoiceRead,
    status_code=201,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def invoice_from_time(
    payload: InvoiceFromTime,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    """Draft invoice from unbilled approved billable time; stamps the entries invoiced."""
    return InvoiceRead.model_validate(await InvoiceService(ctx).from_time(payload))


@router.get(
    "/invoices/{invoice_id}",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def get_invoice(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(await InvoiceService(ctx).get(invoice_id))


@router.patch(
    "/invoices/{invoice_id}",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def update_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceUpdate,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(await InvoiceService(ctx).update(invoice_id, payload))


@router.delete(
    "/invoices/{invoice_id}",
    status_code=204,
    dependencies=[require_permission("invoicing.invoice.delete")],
)
async def delete_invoice(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await InvoiceService(ctx).delete(invoice_id)


@router.post(
    "/invoices/{invoice_id}/issue",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def issue_invoice(
    invoice_id: uuid.UUID,
    payload: InvoiceIssue,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    """Assign the number, freeze the bill-to, open the invoice."""
    return InvoiceRead.model_validate(await InvoiceService(ctx).issue(invoice_id, payload))


@router.post(
    "/invoices/{invoice_id}/send",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.send")],
)
async def send_invoice(
    invoice_id: uuid.UUID,
    payload: DocumentSend,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(await InvoiceService(ctx).send(invoice_id, payload))


@router.post(
    "/invoices/{invoice_id}/remind",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.send")],
)
async def remind_invoice(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    """A payment reminder on demand — same mail, same bookkeeping as the daily cron."""
    return InvoiceRead.model_validate(await InvoiceService(ctx).remind(invoice_id))


@router.post(
    "/invoices/{invoice_id}/cancel",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def cancel_invoice(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(await InvoiceService(ctx).cancel(invoice_id))


@router.post(
    "/invoices/{invoice_id}/credit",
    response_model=InvoiceRead,
    status_code=201,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def credit_invoice(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    """Draft credit note mirroring this invoice with negated prices."""
    return InvoiceRead.model_validate(await InvoiceService(ctx).credit(invoice_id))


@router.post(
    "/invoices/{invoice_id}/payments",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.payment.write")],
)
async def add_payment(
    invoice_id: uuid.UUID,
    payload: PaymentWrite,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(await InvoiceService(ctx).add_payment(invoice_id, payload))


@router.delete(
    "/invoices/{invoice_id}/payments/{payment_id}",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.payment.write")],
)
async def delete_payment(
    invoice_id: uuid.UUID,
    payment_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    return InvoiceRead.model_validate(
        await InvoiceService(ctx).delete_payment(invoice_id, payment_id)
    )


@router.get(
    "/invoices/{invoice_id}/ubl",
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def download_ubl(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """UBL 2.1 XML — importable by Exact Online, SnelStart, Moneybird, e-Boekhouden."""
    service = InvoiceService(ctx)
    invoice = await service.get(invoice_id)
    if invoice.status == InvoiceStatus.DRAFT.value:
        raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
    lines = invoice.lines  # attached by get()
    totals = _totals_from_rows(lines, prices_include_tax=invoice.prices_include_tax)
    seller = (await InvoicingSettingsService(ctx).row()).company_details or {}
    xml = invoice_ubl(invoice, lines, totals, seller)
    filename = f"{invoice.number or invoice_id}.xml"
    return Response(
        content=xml,
        media_type="application/xml",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/invoices/{invoice_id}/refs",
    response_model=list[ExternalRefRead],
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def invoice_refs(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[ExternalRefRead]:
    """What accounting packages know about this invoice (the #31 sync bookkeeping)."""
    await InvoiceService(ctx).repo.get_or_404(invoice_id)
    refs = await ExternalRefService(ctx).list_for("invoice", invoice_id)
    return [ExternalRefRead.model_validate(r) for r in refs]


@router.post(
    "/invoices/{invoice_id}/export",
    response_model=ExternalRefRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def export_invoice(
    invoice_id: uuid.UUID,
    provider: str = Query(..., description="a registered accounting provider key"),
    ctx: RequestContext = Depends(require_context),
) -> ExternalRefRead:
    """Push this invoice to a live accounting provider (#31). Until an adapter module is
    installed the registry is empty and this reports the provider as unknown — UBL download
    is the always-available path."""
    adapter = accounting.get_provider(provider)
    if adapter is None:
        raise AppError(
            "validation", "errors.invoicing.provider_unknown", status_code=400,
            fields={"provider": "errors.invoicing.provider_unknown"},
        )
    service = InvoiceService(ctx)
    invoice = await service.get(invoice_id)
    if invoice.status == InvoiceStatus.DRAFT.value:
        raise AppError("conflict", "errors.invoicing.wrong_status", status_code=409)
    seller = (await InvoicingSettingsService(ctx).row()).company_details or {}
    result = await adapter.export_invoice(ctx, invoice, seller)
    ref = await ExternalRefService(ctx).upsert(
        provider=provider, local_type="invoice", local_id=invoice.id,
        external_id=result.external_id, payload=result.payload,
    )
    return ExternalRefRead.model_validate(ref)


# --- quotes -------------------------------------------------------------------------- #
@router.get(
    "/quotes",
    response_model=Page[QuoteRead],
    dependencies=[require_permission("invoicing.quote.read")],
)
async def list_quotes(
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status: str | None = Query(
        None, description="draft | open | accepted | rejected | expired | invoiced"
    ),
    company_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, description="matches number and reference"),
    sort: str | None = Query(
        None, description="number | status | issue_date | valid_until | total | created_at"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[QuoteRead]:
    items, total = await QuoteService(ctx).list(
        limit=limit, offset=offset, status=status, company_id=company_id, q=q, sort=sort
    )
    return Page(
        items=[QuoteRead.model_validate(i) for i in items],
        total=total, limit=limit, offset=offset,
    )


@router.post(
    "/quotes",
    response_model=QuoteRead,
    status_code=201,
    dependencies=[require_permission("invoicing.quote.write")],
)
async def create_quote(
    payload: QuoteCreate,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).create(payload))


@router.get(
    "/quotes/{quote_id}",
    response_model=QuoteRead,
    dependencies=[require_permission("invoicing.quote.read")],
)
async def get_quote(
    quote_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).get(quote_id))


@router.patch(
    "/quotes/{quote_id}",
    response_model=QuoteRead,
    dependencies=[require_permission("invoicing.quote.write")],
)
async def update_quote(
    quote_id: uuid.UUID,
    payload: QuoteUpdate,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).update(quote_id, payload))


@router.delete(
    "/quotes/{quote_id}",
    status_code=204,
    dependencies=[require_permission("invoicing.quote.delete")],
)
async def delete_quote(
    quote_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await QuoteService(ctx).delete(quote_id)


@router.post(
    "/quotes/{quote_id}/issue",
    response_model=QuoteRead,
    dependencies=[require_permission("invoicing.quote.write")],
)
async def issue_quote(
    quote_id: uuid.UUID,
    payload: InvoiceIssue,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).issue(quote_id, payload))


@router.post(
    "/quotes/{quote_id}/send",
    response_model=QuoteRead,
    dependencies=[require_permission("invoicing.quote.send")],
)
async def send_quote(
    quote_id: uuid.UUID,
    payload: DocumentSend,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).send(quote_id, payload))


@router.post(
    "/quotes/{quote_id}/accept",
    response_model=QuoteRead,
    dependencies=[require_permission("invoicing.quote.write")],
)
async def accept_quote(
    quote_id: uuid.UUID,
    payload: QuoteDecision,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).decide(quote_id, True, payload))


@router.post(
    "/quotes/{quote_id}/reject",
    response_model=QuoteRead,
    dependencies=[require_permission("invoicing.quote.write")],
)
async def reject_quote(
    quote_id: uuid.UUID,
    payload: QuoteDecision,
    ctx: RequestContext = Depends(require_context),
) -> QuoteRead:
    return QuoteRead.model_validate(await QuoteService(ctx).decide(quote_id, False, payload))


@router.post(
    "/quotes/{quote_id}/convert",
    response_model=InvoiceRead,
    status_code=201,
    dependencies=[require_permission("invoicing.quote.write")],
)
async def convert_quote(
    quote_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    """Accepted quote → draft invoice carrying the lines at their accepted prices."""
    return InvoiceRead.model_validate(await QuoteService(ctx).convert(quote_id))
