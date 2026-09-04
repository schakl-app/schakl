"""REST endpoints for invoicing under ``/api/v1/invoicing`` (issue #207).

Every route declares its permission (deny-by-default, §15). Static segments are declared
before ``/{invoice_id}`` so "settings"/"summary" never match an id path param.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile

from app.core.entitlements.service import license_exempt
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.invoicing import accounting
from app.modules.invoicing.models import InvoiceStatus
from app.modules.invoicing.payments import InvoicePaymentService, handle_webhook
from app.modules.invoicing.public import (
    PublicInvoice,
    PublicInvoiceService,
    require_public_invoice,
)
from app.modules.invoicing.render import BUILTIN_DESIGNS, builtin_source, catalog_payload
from app.modules.invoicing.schemas import (
    BacklogGroupBy,
    BacklogSourceFilter,
    DocumentSend,
    ExternalRefRead,
    InvoiceCreate,
    InvoiceFromTime,
    InvoiceIssue,
    InvoicePaymentAccountRead,
    InvoicePaymentIntentCreate,
    InvoicePaymentIntentRead,
    InvoicePaymentRefresh,
    InvoiceRead,
    InvoiceUpdate,
    InvoicingSettingsRead,
    InvoicingSettingsWrite,
    InvoicingSummary,
    OriginalsBatchReport,
    OutstandingRead,
    PaymentWrite,
    ProductCreate,
    ProductRead,
    ProductUpdate,
    PublicCheckout,
    PublicInvoiceRead,
    QrPreview,
    QuoteCreate,
    QuoteDecision,
    QuoteRead,
    QuoteUpdate,
    RecurringBacklogReport,
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
)
from app.modules.invoicing.ubl import invoice_ubl
from app.schemas import Page

router = APIRouter(prefix="/invoicing", tags=["invoicing"])

#: A document read. Scoped since #266, so this is the *floor*: an ``:own`` holder — the
#: ``client`` role — passes here, and the company horizon plus ``InvoiceService`` decide
#: which documents that actually means.
_READ = "invoicing.invoice.read"
#: The invoicing **module**, as opposed to a document (#266). Every org-wide surface under
#: ``_READ`` — the seller identity and bank details, the price list, the template library,
#: the unbilled-hours backlog, the accounting sync's bookkeeping — declares ``:any``, so an
#: ``:own`` holder cannot reach it. These are not rows a company horizon could narrow: there
#: is no client whose price list this is, so the scope is the only thing that can fence them.
#: Staff are unaffected — a legacy bare grant and ``:any`` both satisfy it; only ``:own``
#: alone does not.
_MODULE = "any"

#: The most documents one archive may hold (#307).
#:
#: Deliberately **not** the bulk selection's own 200 (``core.bulk.schemas.MAX_BULK_IDS``):
#: every entry here is a full WeasyPrint layout, tens of milliseconds on a developer's machine
#: and several times that on the small VPS an agency self-hosts on, so two hundred of them is a
#: request no proxy in front of this will wait out — and one with no progress to show for it.
#: Fifty is the pager's default page size, so "tick the page, download it" is exactly what fits,
#: and it keeps the id list comfortably inside a URL. ``MAX_IMPORT_ROWS``' reasoning applied to
#: the other synchronous batch: a cap is what keeps this path honest until archives are a
#: background job (issue #77's sibling).
MAX_ARCHIVE_DOCUMENTS = 50


# --- settings ----------------------------------------------------------------- #
@router.get(
    "/settings",
    response_model=InvoicingSettingsRead,
    dependencies=[require_permission(_READ, _MODULE)],
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
    dependencies=[require_permission(_READ, _MODULE)],
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
    dependencies=[require_permission(_READ, _MODULE)],
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


#: A rendered document is a standalone page: no scripts of its own to allow, no fetches to
#: make (its images are inlined as data URIs), and framable only by the app that renders it.
#: The web proxy re-states the same policy on its side, because a browser reads the header on
#: the response it actually loaded — and that is the proxy's, not ours.
_PREVIEW_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'self'"
    ),
    "X-Frame-Options": "SAMEORIGIN",
    "Cache-Control": "no-store",
}


# --- templates ------------------------------------------------------------------ #
@router.get(
    "/templates",
    response_model=list[TemplateRead],
    dependencies=[require_permission(_READ, _MODULE)],
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
    "/templates/qr-preview",
    response_model=QrPreview,
    dependencies=[require_permission("invoicing.settings.manage")],
)
async def preview_template_qr(
    payload: TemplatePreview,
    ctx: RequestContext = Depends(require_context),
) -> QrPreview:
    """The payment QR alone, as an unsaved config would draw it (#305).

    Its own route beside ``/templates/preview`` because the colour picker needs an answer per
    keystroke and a full document render is a Jinja pass over a sample invoice. It also carries
    what the whole-page preview cannot show at 3cm: whether ``readable_pair`` substituted, so
    the editor can say *why* the colour on screen is not the colour that was typed.
    """
    return QrPreview.model_validate(await TemplateService(ctx).qr_preview(payload.config))


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
        content=await TemplateService(ctx).preview(payload.config, payload.template_id),
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
    dependencies=[require_permission(_READ)],
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
    # Typed, not parsed by hand: a malformed date was a 500 out of `date.fromisoformat`
    # rather than the 422 every other bad query param gets.
    until: date | None = Query(None, description="org-local date (YYYY-MM-DD), inclusive"),
    ctx: RequestContext = Depends(require_context),
) -> UnbilledRead:
    data = await InvoiceService(ctx).unbilled(company_id, project_id=project_id, until=until)
    return UnbilledRead.model_validate(data)


@router.get(
    "/outstanding",
    response_model=OutstandingRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def outstanding(
    company_id: uuid.UUID = Query(...),
    ctx: RequestContext = Depends(require_context),
) -> OutstandingRead:
    """Everything a client still has to be invoiced for: hours, agreement periods, renewals.

    The source the editor's three sections pick from, in one round trip. Periods a document
    already claims are marked ``already_billed`` rather than omitted, so "did I invoice March
    yet?" is answered on the picker instead of by a duplicate a week later. On
    ``invoice.write`` and not ``.read``: this is a build-an-invoice surface, not a report.
    """
    return OutstandingRead.model_validate(await InvoiceService(ctx).outstanding(company_id))


@router.get(
    "/uninvoiced",
    response_model=UninvoicedReport,
    dependencies=[require_permission(_READ, _MODULE)],
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


@router.get(
    "/recurring-backlog",
    response_model=RecurringBacklogReport,
    dependencies=[require_permission(_READ, _MODULE)],
)
async def recurring_backlog(
    group: BacklogGroupBy = Query("company", description="company | month | source"),
    source: BacklogSourceFilter = Query("all", description="all | subscription | domain"),
    limit: int = Query(500, ge=1, le=1000, description="cap on the item detail, not the totals"),
    ctx: RequestContext = Depends(require_context),
) -> RecurringBacklogReport:
    """Org-wide recurring work still to invoice (#302): agreement periods and domain renewals
    that no document claims yet.

    The other half of "nog te factureren" — ``/uninvoiced`` answers it for hours. Read-only
    and on ``.read`` for the same reason that one is: browsing the backlog is a view, and
    building the invoice stays a ``.write`` act in the editor.
    """
    return RecurringBacklogReport.model_validate(
        await InvoiceService(ctx).recurring_backlog(group=group, source=source, limit=limit)
    )


# --- invoices ------------------------------------------------------------------------ #
@router.get(
    "/invoices",
    response_model=Page[InvoiceRead],
    dependencies=[require_permission(_READ)],
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


@router.get(
    "/invoices/pdf",
    dependencies=[require_permission(_READ)],
)
async def download_invoices_zip(
    ids: list[uuid.UUID] = Query(
        ...,
        min_length=1,
        max_length=MAX_ARCHIVE_DOCUMENTS,
        description="The invoices to pack, by id — the list screen's ✎ selection (#307).",
    ),
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """A selection of invoices as one zip of PDFs — the bulk half of ``/{invoice_id}/pdf``.

    **A GET, and that is load-bearing twice over.** It is a read: past a licence's expiry a
    module goes read-only, not gone, and ``license_write_gate`` reads the method — a POST here
    would 402 an agency out of its own paperwork at exactly the moment it wants to hand it to
    an accountant. It is also idempotent and cacheable-in-principle, which a download is.

    Ids the caller may not read are **absent**, not an error (``InvoiceService.by_ids``); an
    empty result is a 404, because "here is your archive of nothing" is not an answer. Drafts
    print like they do one at a time — the list offers this only for documents that exist, the
    same rule its row menu follows, and this route has no second opinion about it.
    """
    service = InvoiceService(ctx)
    invoices = await service.by_ids(ids)
    if not invoices:
        raise AppError("not_found", "errors.not_found", status_code=404)
    content, filename = await service.documents_zip(invoices, "invoice")
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
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
    dependencies=[require_permission(_READ)],
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


# --- online payments (epic #269) ----------------------------------------------- #
@router.get(
    "/payment-accounts",
    response_model=list[InvoicePaymentAccountRead],
    dependencies=[require_permission("invoicing.payment.link", _MODULE)],
)
async def list_payment_accounts(
    ctx: RequestContext = Depends(require_context),
) -> list[InvoicePaymentAccountRead]:
    """Which payment credentials this org has connected, across every enabled provider module.

    ``:any``, not the floor (#266): this is org-wide configuration — no client's row could be
    narrowed to it — so a client-role ``:own`` holder must not reach it even though they may
    *start* a payment. What the portal needs instead is ``InvoiceRead.online_payment``, which
    answers the only question a payer has ("can I pay this here?") without naming an account.
    The response carries a label, a mode and an id; never a credential.
    """
    return [
        InvoicePaymentAccountRead.model_validate(account, from_attributes=True)
        for account in await InvoicePaymentService(ctx).accounts()
    ]


@router.post(
    "/invoices/{invoice_id}/payment-intents",
    response_model=InvoicePaymentIntentRead,
    dependencies=[require_permission("invoicing.payment.link")],
)
async def start_payment(
    invoice_id: uuid.UUID,
    payload: InvoicePaymentIntentCreate,
    ctx: RequestContext = Depends(require_context),
) -> InvoicePaymentIntentRead:
    """Open a hosted checkout for this invoice's outstanding balance.

    The amount is the server's to decide — the body carries only *which* credential to use.
    """
    return InvoicePaymentIntentRead.model_validate(
        await InvoicePaymentService(ctx).start(invoice_id, payload)
    )


@router.post(
    "/invoices/{invoice_id}/payment-intents/refresh",
    response_model=InvoicePaymentRefresh,
    dependencies=[require_permission("invoicing.payment.link")],
)
@license_exempt(
    "Finding out whether money that has already left a client's bank account arrived is the "
    "webhook's argument one step removed: an expired licence makes a module read-only, it "
    "does not make a payment already made unknowable to the person who made it."
)
async def refresh_payments(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoicePaymentRefresh:
    """"Did my payment land?" — asked by the page a payer returns to (#304).

    ``:own`` at the floor, unlike ``sync`` beside it, and the difference is the whole point.
    ``sync`` is the *operator's* repair action: it spends a provider call on any attempt on
    demand, so it stays ``:any``. This one is the **payer** finding out what happened to their
    own money, so a client must be able to reach it — and it is bounded instead of trusted:
    non-final attempts only, throttled per attempt on ``synced_at``, and free when there is
    nothing in flight.
    """
    asked, latest = await InvoicePaymentService(ctx).refresh_pending(invoice_id)
    invoice = await InvoiceService(ctx).get(invoice_id)
    return InvoicePaymentRefresh(
        changed=asked,
        status=latest.status if latest is not None else None,
        settled=latest is not None and latest.settled_at is not None,
        invoice_status=invoice.status,
    )


@router.get(
    "/invoices/{invoice_id}/payment-intents",
    response_model=list[InvoicePaymentIntentRead],
    dependencies=[require_permission(_READ)],
)
async def list_payment_intents(
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[InvoicePaymentIntentRead]:
    """This invoice's payment attempts. ``_READ``'s floor, not ``_MODULE``: a client must be
    able to see the state of the payment they just made."""
    return [
        InvoicePaymentIntentRead.model_validate(intent)
        for intent in await InvoicePaymentService(ctx).list_for(invoice_id)
    ]


@router.post(
    "/invoices/{invoice_id}/payment-intents/{intent_id}/sync",
    response_model=InvoicePaymentIntentRead,
    dependencies=[require_permission("invoicing.payment.link", _MODULE)],
)
async def sync_payment_intent(
    invoice_id: uuid.UUID,
    intent_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InvoicePaymentIntentRead:
    """Re-ask the provider about one attempt, by hand.

    "Sync failures are surfaced and retryable, not silently dropped" (#267) needs a button as
    well as a cron: a callback that never arrived — a firewall, a Zero Trust rule, an outage —
    is fixed by an operator who can then settle the payment without waiting for the next pass.

    ``:any``, unlike starting a payment: this is a **repair** action, it spends an outbound
    call to the provider on every press, and a client has no use for it — their own status
    arrives by callback and, failing that, by the hourly reconcile. Leaving it at the floor
    would have put a rate-costed external call behind a button on a client-reachable page.
    """
    service = InvoicePaymentService(ctx)
    intents = await service.list_for(invoice_id)
    intent = next((i for i in intents if i.id == intent_id), None)
    if intent is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return InvoicePaymentIntentRead.model_validate(await service.reconcile(intent))


@router.post(
    "/payments/webhook/{provider}/{token}",
    dependencies=[
        no_permission_required(
            "Payment-provider callback; authenticated by our own per-account token "
            "(org + account + secret) and, decisively, by re-fetching the payment from the "
            "provider with the tenant's credential — never by a user session"
        )
    ],
)
@license_exempt(
    "Money the client has already paid is recorded whatever the licence says. A 402 here "
    "would take a payment that left someone's bank account and drop it — an expired licence "
    "makes a module read-only, it does not make the agency's takings disappear."
)
async def payment_webhook(provider: str, token: str, request: Request) -> Response:
    """The provider's callback. Returns bare statuses and no body, by design.

    ``200`` also covers a reference this tenant does not know — a provider must not be able to
    enumerate what exists here by reading status codes (Mollie documents exactly this).
    """
    status = await handle_webhook(provider, token, await request.body(), request.headers)
    return Response(status_code=status)


@router.get(
    "/invoices/{invoice_id}/pdf",
    dependencies=[require_permission(_READ)],
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


@router.post(
    "/invoices/originals",
    response_model=OriginalsBatchReport,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def attach_invoice_originals(
    file: UploadFile = File(..., description="A zip of PDFs, each named after its invoice number"),
    ctx: RequestContext = Depends(require_context),
) -> OriginalsBatchReport:
    """Attach the original documents of **imported** invoices in one go (docs/INVOICING.md).

    Each PDF in the archive is matched to an imported invoice by its file name — exactly the
    number, or a name containing it, separators and case ignored — and attached where that
    invoice holds no original yet. The report names every file that matched, matched two
    numbers, matched none, or was not a PDF, and every invoice left alone because it already
    had one. Multipart, so off the MCP surface; the JSON twin is one ``POST /files/inline``
    against the invoice plus ``PATCH {original_file_id}``.
    """
    data = await file.read()
    report = await InvoiceService(ctx).attach_originals(data)
    return OriginalsBatchReport.model_validate(report)


@router.post(
    "/invoices/{invoice_id}/original",
    response_model=InvoiceRead,
    dependencies=[require_permission("invoicing.invoice.write")],
)
async def attach_invoice_original(
    invoice_id: uuid.UUID,
    file: UploadFile = File(..., description="The PDF the client actually received"),
    ctx: RequestContext = Depends(require_context),
) -> InvoiceRead:
    """Attach (or replace) the original document of an **imported** invoice.

    Stored untouched, fingerprinted on the invoice itself, and served in place of a render by
    every reader — the download, the mail attachment, the public link and the portal. A native
    invoice refuses (409): its document *is* its render. Multipart, so off the MCP surface; an
    agent uploads through ``POST /files/inline`` and names the file in ``PATCH``.
    """
    data = await file.read()
    service = InvoiceService(ctx)
    invoice = await service.attach_original(
        invoice_id,
        filename=file.filename or "",
        content_type=file.content_type or "application/octet-stream",
        data=data,
    )
    return InvoiceRead.model_validate(await service.get(invoice.id))


@router.get(
    "/invoices/{invoice_id}/preview",
    response_class=Response,
    dependencies=[require_permission(_READ)],
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
    dependencies=[require_permission(_READ)],
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
    # Stated totals for an imported document, computed ones for a native — the same answer
    # the PDF and the detail read give (``service.document_totals``).
    totals = service.document_totals(invoice, lines)
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
    dependencies=[require_permission(_READ, _MODULE)],
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


# --------------------------------------------------------------------------- #
# The public invoice link (#304)
# --------------------------------------------------------------------------- #
#: Everything a session-less document response must say, on top of the preview policy.
#:
#: ``Referrer-Policy: no-referrer`` is the one that is not obvious and is the most important
#: line in this block. **The token is in the path**, so every outbound navigation from a page
#: that carries it would leak it in a ``Referer`` header — and the very next thing a payer does
#: is leave for a payment provider. ``strict-origin-when-cross-origin`` (the app's default)
#: strips the path cross-origin but sends the full URL same-origin, which is not enough: what
#: must never travel is the whole credential, and the hop that matters is to a third party.
#: ``no-referrer`` is the only value that guarantees it does not.
#:
#: ``X-Robots-Tag`` because a link mailed to a client ends up in signatures, tickets and
#: helpdesk threads, and a crawler that finds one must not put an invoice in an index.
_PUBLIC_HEADERS = {
    **_PREVIEW_HEADERS,
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
}

#: The non-document version of the same: JSON and PDF carry no CSP worth stating, but they
#: carry the token in their own URL exactly as the page does.
_PUBLIC_META_HEADERS = {
    "Referrer-Policy": "no-referrer",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Cache-Control": "no-store",
}

_PUBLIC_REASON = (
    "The public invoice link (#304). Authenticated by a 256-bit capability token in its own "
    "URL, resolved against the request's tenant with RLS already bound, and answered by a "
    "context that is a client-portal session scoped to that one invoice's company — never by "
    "a user session, and never able to name a second document."
)


@router.get(
    "/public/invoices/{token}",
    response_model=PublicInvoiceRead,
    dependencies=[no_permission_required(_PUBLIC_REASON)],
)
async def public_invoice(
    response: Response,
    public: PublicInvoice = Depends(require_public_invoice),
) -> PublicInvoiceRead:
    """This invoice, as the person holding its link sees it.

    A hand-built narrow shape, never ``InvoiceRead`` — see ``schemas.PublicInvoiceRead`` for
    why a subset-by-omission would have leaked the next field somebody added.
    """
    response.headers.update(_PUBLIC_META_HEADERS)
    return PublicInvoiceRead.model_validate(await PublicInvoiceService(public).read())


@router.get(
    "/public/invoices/{token}/preview",
    response_class=Response,
    dependencies=[no_permission_required(_PUBLIC_REASON)],
)
async def public_invoice_preview(
    public: PublicInvoice = Depends(require_public_invoice),
) -> Response:
    """The rendered document — **the same HTML** the signed-in preview and the PDF produce.

    One artefact, so the page a client opens from a QR can never disagree with the paper it
    was printed on. It is also why the public page draws no document of its own in Svelte.
    """
    service = InvoiceService(public.ctx)
    invoice = await service.get(public.invoice.id)
    return Response(
        content=await service.document_html(invoice, "invoice"),
        media_type="text/html; charset=utf-8",
        headers=_PUBLIC_HEADERS,
    )


@router.get(
    "/public/invoices/{token}/pdf",
    dependencies=[no_permission_required(_PUBLIC_REASON)],
)
async def public_invoice_pdf(
    public: PublicInvoice = Depends(require_public_invoice),
) -> Response:
    """The PDF, for the client who wants it in their own bookkeeping."""
    service = InvoiceService(public.ctx)
    invoice = await service.get(public.invoice.id)
    content, filename = await service.document_pdf(invoice, "invoice")
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            **_PUBLIC_META_HEADERS,
        },
    )


@router.post(
    "/public/invoices/{token}/payment-intents",
    response_model=PublicCheckout,
    dependencies=[no_permission_required(_PUBLIC_REASON)],
)
async def public_start_payment(
    public: PublicInvoice = Depends(require_public_invoice),
) -> PublicCheckout:
    """Open a checkout for what this invoice still owes, and hand back where to go.

    **No body at all**, which is stricter than the signed-in sibling: that one accepts a
    provider/account so an agency running two credentials can say which. A public caller has
    no business naming a credential — the service resolves one and prefers the live over the
    test key (``docs/PAYMENTS.md`` §2) — and no business naming an amount, ever.

    Gated by the module's ordinary licence write gate, like the portal's own pay button. That
    is deliberate symmetry rather than an oversight: an expired licence stops the agency
    *asking* for money on every surface at once, and the two exemptions that exist (the
    callback, and the refresh below) are both about money that has **already** moved.
    """
    return PublicCheckout(checkout_url=await PublicInvoiceService(public).start_payment())


@router.post(
    "/public/invoices/{token}/refresh",
    response_model=InvoicePaymentRefresh,
    dependencies=[no_permission_required(_PUBLIC_REASON)],
)
@license_exempt(
    "The same rule as the payment callback: a 402 here would hide money that has already "
    "left someone's bank account from the person who sent it. An expired licence makes a "
    "module read-only; it does not un-happen a payment."
)
async def public_refresh_payments(
    public: PublicInvoice = Depends(require_public_invoice),
) -> InvoicePaymentRefresh:
    """"Did my payment land?", for the payer coming back from a checkout (#304).

    Bounded by ``InvoicePaymentService.refresh_pending`` — the *same* implementation the
    signed-in route uses, so the throttle cannot drift between them: non-final attempts only,
    and at most one provider call per attempt per ``REFRESH_MIN_INTERVAL``, whatever the
    caller does.
    """
    return InvoicePaymentRefresh.model_validate(await PublicInvoiceService(public).refresh())
