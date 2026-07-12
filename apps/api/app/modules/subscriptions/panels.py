"""Subscriptions panel on the company detail view (issue #30 — the modular hub, §6)."""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.subscriptions.service import SubscriptionService
from app.registry import PanelSpec


async def _subscriptions_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    # Money: the panel simply stays empty for someone without the read grant, rather than
    # erroring the whole company page.
    if not ctx.can("subscriptions.subscription.read"):
        return {"subscriptions": [], "forbidden": True}
    subs = await SubscriptionService(ctx).for_company(company_id)
    return {
        "subscriptions": [
            {
                "id": str(s.id),
                "name": s.name,
                "status": s.status,
                "amount": str(s.amount) if s.amount is not None else None,  # type: ignore[attr-defined]
                "currency": s.currency,
                "interval": s.interval,
                "next_invoice_date": (
                    s.next_invoice_date.isoformat() if s.next_invoice_date else None
                ),
            }
            for s in subs
        ],
    }


subscriptions_company_panel = PanelSpec(
    key="subscriptions.company",
    entity_type="company",
    title_key="subscriptions.panel.title",
    provider=_subscriptions_provider,
    position=60,
)
