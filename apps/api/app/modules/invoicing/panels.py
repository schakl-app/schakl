"""Invoicing panel on the company detail view (issue #207 — the modular hub, §6).

Recent invoices with their open balance, plus recent quotes: "who hasn't paid" belongs on
the client, not only in a list page (#31 wanted exactly this panel).
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.invoicing.service import InvoiceService, QuoteService, org_today
from app.registry import PanelSpec


async def _invoicing_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    # Money: the panel stays empty for someone without the read grant rather than erroring
    # the whole company page (the subscriptions-panel stance).
    if not ctx.can("invoicing.invoice.read"):
        return {"invoices": [], "quotes": [], "forbidden": True}
    invoices = await InvoiceService(ctx).for_company(company_id)
    quotes = (
        await QuoteService(ctx).for_company(company_id)
        if ctx.can("invoicing.quote.read")
        else []
    )
    today = await org_today(ctx)
    return {
        "invoices": [
            {
                "id": str(i.id),
                "number": i.number,
                "kind": i.kind,
                "status": i.status,
                "issue_date": i.issue_date.isoformat() if i.issue_date else None,
                "due_date": i.due_date.isoformat() if i.due_date else None,
                "overdue": bool(
                    i.status == "open" and i.due_date is not None and i.due_date < today
                ),
                "total": str(i.total),
                "outstanding": str(i.total - i.paid_total),
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
)
