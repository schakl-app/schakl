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
from app.registry import PROMINENCE_PRIMARY, PanelSpec

#: The panel's default window; the tab lets the user widen it.
_PANEL_RANGE_DAYS = 30


async def _marketing_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("marketing.metrics.read"):
        return {"forbidden": True}
    # ``with_connections`` only here (#411). This panel absorbed the Tag Manager card, so it is
    # the one surface that draws what is *measuring* the client's site beside what the numbers
    # say — and the extra statement behind it is one the tab and the portal widget must not pay.
    data = await MarketingService(ctx).company_marketing(
        company_id, _PANEL_RANGE_DAYS, with_connections=True
    )
    return data.model_dump(mode="json")


marketing_company_panel = PanelSpec(
    key="marketing.overview",
    entity_type="company",
    title_key="marketing.panel.title",
    provider=_marketing_provider,
    position=50,
    # No declaration on purpose (#365): the provider already refuses the metrics themselves,
    # and what is left — "no Google connection yet", "ask someone who may link accounts" — is a
    # refusal the reader can *act on*. docs/UX.md's own exemption: omit the declaration where
    # the panel deliberately draws a state worth telling apart from an empty one.
    explicit_public="draws its own refusal; the metrics self-check marketing.metrics.read",
    prominence=PROMINENCE_PRIMARY,
    # A client with a container and no metrics source still has something to say, so the
    # connections row counts as content (#411) — folding it away would put back exactly the
    # warning the deleted Tag Manager card existed to carry.
    # SIZE_FULL stays, and now with its reason stated (#438): this is the one panel that is a
    # dashboard rather than a card — a metrics grid plus per-source rows — and halving it
    # would fold the grid to a column. Every other panel is half.
    empty_when=lambda data: not data.get("sources")
    and not data.get("connections")
    and not data.get("forbidden"),
)
