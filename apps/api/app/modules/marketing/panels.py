"""The marketing panel on a company's detail page (epic #134).

A ``PanelSpec`` provider, so it composes into the company hub via the registry with no edit to
the company page (CLAUDE.md §6). It hands back a 30-day-vs-previous-30 summary read **entirely
from our database** — one query for every link's daily rows, zero Google calls
(docs/PERFORMANCE.md). The matching web component (``marketing.overview`` key) renders the KPI
rows, sparklines and the link-management edit mode; a member without ``marketing.metrics.read``
gets a forbidden marker instead, like the Drive panel.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.marketing.service import MarketingService
from app.registry import PanelSpec

#: The panel's default window; the tab lets the user widen it.
_PANEL_RANGE_DAYS = 30


async def _marketing_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("marketing.metrics.read"):
        return {"forbidden": True}
    data = await MarketingService(ctx).company_marketing(company_id, _PANEL_RANGE_DAYS)
    return data.model_dump(mode="json")


marketing_company_panel = PanelSpec(
    key="marketing.overview",
    entity_type="company",
    title_key="marketing.panel.title",
    provider=_marketing_provider,
    position=50,
)
