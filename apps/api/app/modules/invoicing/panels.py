"""Invoicing panel on the company detail view (issue #207 — the modular hub, §6).

Recent invoices with their open balance, plus recent quotes: "who hasn't paid" belongs on
the client, not only in a list page (#31 wanted exactly this panel).
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.invoicing.calc import outstanding_of
from app.modules.invoicing.service import InvoiceService, QuoteService, org_today
from app.registry import SIZE_HALF, PanelSpec


async def _invoicing_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    # The panel *declares* ``invoicing.invoice.read`` (#365), so the composer never calls this
    # for a caller who lacks it and the old in-provider check is gone: two gates that must agree
    # is how they drift. The base key, so an ``:own`` holder — a client on their own company
    # page (#266) — still gets the panel; ``for_company`` then leaves the agency's drafts out.
    invoices, invoice_total = await InvoiceService(ctx).for_company(company_id)
    quotes, quote_total = (
        await QuoteService(ctx).for_company(company_id)
        if ctx.can("invoicing.quote.read")
        else ([], 0)
    )
    today = await org_today(ctx)
    return {
        # The whole ledger's size, never the page's (#407). This panel used to cut at eight
        # invoices and five quotes in silence *and* offer no way through to the rest, so a
        # client with sixty documents and a client with six drew the same card.
        "invoice_total": invoice_total,
        "quote_total": quote_total,
        "invoices": [
            {
                "id": str(i.id),
                "number": i.number,
                "kind": i.kind,
                "status": i.status,
                "issue_date": i.issue_date.isoformat() if i.issue_date else None,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                # A draft has no issue date and no number, so the day it was started is the
                # one fact that tells five drafts apart on the card.
                "created_at": i.created_at.isoformat(),
                "overdue": bool(
                    i.status == "open"
                    and i.due_date is not None
                    and i.due_date < today
                    and outstanding_of(i) > 0
                ),
                "total": str(i.total),
                "outstanding": str(outstanding_of(i)),
                "credited": bool(i.credited_total),
                "currency": i.currency,
            }
            for i in invoices
        ],
        "quotes": [
            {
                "id": str(q.id),
                "number": q.number,
                "status": q.status,
                "valid_until": q.valid_until.isoformat() if q.valid_until else None,
                "total": str(q.total),
                "currency": q.currency,
            }
            for q in quotes
        ],
    }


invoicing_company_panel = PanelSpec(
    key="invoicing.company",
    entity_type="company",
    title_key="invoicing.panel.title",
    provider=_invoicing_provider,
    position=65,
    requires_permission="invoicing.invoice.read",
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("invoices") and not data.get("quotes"),
)
