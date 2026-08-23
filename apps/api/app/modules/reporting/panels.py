"""The reporting panel on a company's detail page (issue #300).

A ``PanelSpec``, so it composes into the company hub through the registry with no edit to the
company page (CLAUDE.md §6). It answers two questions an account manager has while looking at
a client: *did last month's report go out*, and *when is the next one*.

Cheap by construction: the latest few rows plus the profile, read from our own tables — no
Google call, no SE Ranking call, no render (docs/PERFORMANCE.md). It is also what a **portal**
login sees on their own company page, which is why it goes through ``ReportService`` rather
than a hand-built select: the portal repository is what keeps a client to their own published
client-facing reports.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.reporting.models import ReportAudience
from app.modules.reporting.service import ProfileService, ReportService
from app.registry import PANEL_FEED, SIZE_HALF, PanelSpec

#: The feed default (#407). Six was its own number; the count beside it is the point.
_RECENT = PANEL_FEED


async def _reporting_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    # Declared on the spec (#365) — the composer never calls this without the read grant.
    # ``count=True`` (#407): the panel drew six documents with nothing to say whether the
    # client has six or sixty, and the count is one indexed query over a table already keyed
    # on ``(org_id, company_id)``.
    reports = await ReportService(ctx).list(
        company_id=company_id, limit=_RECENT, count=True
    )
    payload: dict = {
        "total": reports.total,
        "reports": [row.model_dump(mode="json") for row in reports.items],
        "can_manage": ctx.can("reporting.profile.manage"),
        "can_send": ctx.can("reporting.report.send"),
    }
    # A client has no schedule to read: it is the agency's arrangement, not theirs.
    if ctx.can("reporting.profile.manage") and not ctx.is_portal:
        profile = await ProfileService(ctx).get(company_id)
        payload["schedule"] = profile.effective_schedule
        payload["next_run_on"] = (
            profile.next_run_on.isoformat() if profile.next_run_on else None
        )
        payload["recipients"] = profile.recipients
        payload["configured"] = profile.id != uuid.UUID(int=0)
    return payload


reporting_company_panel = PanelSpec(
    key="reporting.reports",
    entity_type="company",
    title_key="reporting.panel.title",
    provider=_reporting_provider,
    position=55,
    requires_permission="reporting.report.read",
    size=SIZE_HALF,
    # "No report yet **and** no schedule" is nothing-yet; a configured client with an empty
    # history has a next run to show, which is news.
    empty_when=lambda data: not data.get("reports") and not data.get("configured"),
)

__all__ = ["ReportAudience", "reporting_company_panel"]
