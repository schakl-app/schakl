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
from app.modules.invoicing.render import BUILTIN_DESIGNS, builtin_source, catalog_payload
from app.modules.invoicing.schemas import (
    BillableSubscription,
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
    ProductCreate,
    ProductRead,
    ProductUpdate,
    QuoteCreate,
    QuoteDecision,
    QuoteRead,
    QuoteUpdate,
    TaxRateCreate,
    TaxRateRead,
    TaxRateUpdate,
    TemplateCatalog,
    TemplateCreate,
    TemplatePreview,
    TemplateRead,
    TemplateSource,
    TemplateUpdate,
    UnbilledRead,
    UninvoicedGroupBy,
    UninvoicedReport,
)
from app.modules.invoicing.service import (
    ExternalRefService,
    InvoiceService,
    InvoicingSettingsService,
    ProductService,
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


# --- products (owner request): default line presets ----------------------------- #
@router.get(
    "/products",
    response_model=list[ProductRead],
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def list_products(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[ProductRead]:
    items = await ProductService(ctx).list(include_inactive=include_inactive)
    return [ProductRead.model_validate(p) for p in items]


@router.post(
    "/products",
    response_model=ProductRead,
    status_code=201,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def create_product(
    payload: ProductCreate,
    ctx: RequestContext = Depends(require_context),
) -> ProductRead:
    return ProductRead.model_validate(await ProductService(ctx).create(payload))


@router.patch(
    "/products/{product_id}",
    response_model=ProductRead,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def update_product(
    product_id: uuid.UUID,
    payload: ProductUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ProductRead:
    return ProductRead.model_validate(await ProductService(ctx).update(product_id, payload))


@router.delete(
    "/products/{product_id}",
    status_code=204,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def delete_product(
    product_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ProductService(ctx).delete(product_id)


#: A rendered document is a standalone page: it must not become the frame of another site,
#: and it has no scripts of its own to allow. The preview iframe sets the same on its side.
_PREVIEW_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'",
    "X-Frame-Options": "SAMEORIGIN",
    "Cache-Control": "no-store",
}


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


@router.get(
    "/template-blocks",
    response_model=TemplateCatalog,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def template_blocks(ctx: RequestContext = Depends(require_context)) -> TemplateCatalog:
    """What a template may rearrange: the block/field catalog plus the shipped designs.

    Keys only — the editor resolves `invoicing.block.*` / `invoicing.field.*` in the
    *viewer's* locale, because the API does not pick a locale for someone else's screen
    (§17's rule). ``can_author`` is here so the editor can hide the HTML/CSS tab rather than
    offer a control whose save will 403; the API is still the boundary (§15).
    """
    return TemplateCatalog(
        blocks=catalog_payload(),
        designs=list(BUILTIN_DESIGNS),
        can_author=ctx.can("invoicing.template.author"),
    )


@router.get(
    "/template-blocks/{design}/source",
    response_model=TemplateSource,
    dependencies=[require_permission("invoicing.template.author")],
)
async def template_source(design: str) -> TemplateSource:
    """A shipped design's own HTML and CSS, to start a custom template from.

    Writing one from a blank page means knowing the whole render context by heart; branching
    from the design they already like means changing the two things they want changed. These
    are the same files the shipped design renders from, so what they get is what they saw.
    """
    html, css = builtin_source(design)
    return TemplateSource(html=html, css=css)


@router.post(
    "/templates/preview",
    response_class=Response,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def preview_template(
    payload: TemplatePreview,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """Render a **sample** document with an unsaved config — the editor's live preview.

    Against a sample rather than a real invoice on purpose: the editor is reached from
    Settings, where no document is in hand, and a design must be judged on one that exercises
    every block (two line kinds, a paid amount, a VAT split) rather than on whichever invoice
    happened to be first. It renders the tenant's real seller identity and branding, because
    those are what the design has to sit around.
    """
    return Response(
        content=await TemplateService(ctx).preview(payload.config),
        media_type="text/html; charset=utf-8",
        headers=_PREVIEW_HEADERS,
    )


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


@router.get(
    "/billable-subscriptions",
    response_model=list[BillableSubscription],
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def billable_subscriptions(
    company_id: uuid.UUID = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> list[BillableSubscription]:
    """A client's active agreements as ready-made invoice lines (the "＋ abonnement" pick).

    ``already_billed`` marks a period a document already claims: shown rather than hidden,
    so the answer to "did I invoice March yet?" is on the picker instead of on a duplicate.
    """
    rows = await InvoiceService(ctx).billable_subscriptions(company_id)
    return [BillableSubscription.model_validate(row) for row in rows]


@router.get(
    "/uninvoiced",
    response_model=UninvoicedReport,
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def uninvoiced(
    group: UninvoicedGroupBy = Query(
        "company", description="day | week | month | year | company | project | user"
    ),
    limit: int = Query(500, ge=1, le=1000, description="cap on the entry detail, not the totals"),
    ctx: RequestContext = Depends(require_context),
) -> UninvoicedReport:
    """Org-wide report of approved + billable + not-yet-invoiced hours (#277), bucketed
    server-side with exact per-group subtotals. Read-only: the per-company ``/unbilled``
    stays the invoice-build preview, and building happens via ``/invoices/from-time``."""
    return UninvoicedReport.model_validate(
        await InvoiceService(ctx).uninvoiced_report(group=group, limit=limit)
    )


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
    lines: bool = Query(
        True,
        description="Include each row's lines and tax groups. False for list views (#290).",
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[InvoiceRead]:
    items, total = await InvoiceService(ctx).list(
        limit=limit, offset=offset, status=status, company_id=company_id,
        kind=kind, overdue=overdue, q=q, sort=sort, lines=lines,
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
    "/invoices/{invoice_id}/pdf",
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def download_invoice_pdf(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """The rendered invoice document (owner feedback): the same PDF the send path attaches."""
    service = InvoiceService(ctx)
    invoice = await service.get(invoice_id)
    content, filename = await service.document_pdf(invoice, "invoice")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/quotes/{quote_id}/pdf",
    dependencies=[require_permission("invoicing.quote.read")],
)
async def download_quote_pdf(
    quote_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    service = QuoteService(ctx)
    quote = await service.get(quote_id)
    content, filename = await service.document_pdf(quote, "quote")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/invoices/{invoice_id}/preview",
    response_class=Response,
    dependencies=[require_permission("invoicing.invoice.read")],
)
async def preview_invoice(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """The invoice as HTML — **the same artefact** ``/pdf`` prints.

    The detail page and the print route render this in a frame rather than drawing the
    document a second time in Svelte. That is what makes "the preview and the PDF disagree"
    unrepresentable, and it is the only way a tenant's own HTML template can be previewed at
    all: a Svelte component cannot render someone else's Jinja.
    """
    service = InvoiceService(ctx)
    invoice = await service.get(invoice_id)
    return Response(
        content=await service.document_html(invoice, "invoice"),
        media_type="text/html; charset=utf-8",
        headers=_PREVIEW_HEADERS,
    )


@router.get(
    "/quotes/{quote_id}/preview",
    response_class=Response,
    dependencies=[require_permission("invoicing.quote.read")],
)
async def preview_quote(
    quote_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    service = QuoteService(ctx)
    quote = await service.get(quote_id)
    return Response(
        content=await service.document_html(quote, "quote"),
        media_type="text/html; charset=utf-8",
        headers=_PREVIEW_HEADERS,
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
    lines: bool = Query(
        True,
        description="Include each row's lines and tax groups. False for list views (#290).",
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[QuoteRead]:
    items, total = await QuoteService(ctx).list(
        limit=limit, offset=offset, status=status, company_id=company_id, q=q, sort=sort,
        lines=lines,
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
