"""What ``uptime`` attaches to a company (CLAUDE.md §6, the modular hub).

One panel, and it is a **summary** rather than a list. A client with forty monitors would
otherwise push every other panel off the company page, and the question the page is being asked
is "is anything wrong here", which is a count. The monitor list itself lives one click away with
the shared pager under it (§9).
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.tenancy import RequestContext
from app.registry import SIZE_HALF, PanelSpec


async def company_uptime(ctx: RequestContext, company_id: uuid.UUID) -> dict[str, Any]:
    """Monitors for this client, folded in one grouped query.

    Returns the counts even when they are all zero: a company with no monitoring is a fact worth
    showing on the page, and an empty panel that renders nothing looks like a broken panel.
    """
    from app.integrations.uptime.service import UptimeService

    # The permission is declared on the spec (#365), so the composer drops the panel before it
    # calls this — no second copy of the rule to drift from the first.
    summary = await UptimeService(ctx).company_summary(company_id)
    return {**summary, "visible": True}


UPTIME_PANELS: list[PanelSpec] = [
    PanelSpec(
        key="uptime.company",
        entity_type="company",
        title_key="uptime.panel.title",
        provider=company_uptime,
        position=460,
        requires_permission="uptime.monitor.read",
        size=SIZE_HALF,
        empty_when=lambda data: not data.get("total"),
    ),
]
