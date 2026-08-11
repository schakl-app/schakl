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
from app.registry import PanelSpec


async def company_uptime(ctx: RequestContext, company_id: uuid.UUID) -> dict[str, Any]:
    """Monitors for this client, folded in one grouped query.

    Returns the counts even when they are all zero: a company with no monitoring is a fact worth
    showing on the page, and an empty panel that renders nothing looks like a broken panel.
    """
    from app.modules.uptime.service import UptimeService

    if not ctx.can("uptime.monitor.read"):
        # The panel composer renders what it is given; a caller without the permission gets no
        # data rather than a 403 that would take the whole company page down with it.
        return {"total": 0, "by_status": {}, "visible": False}
    summary = await UptimeService(ctx).company_summary(company_id)
    return {**summary, "visible": True}


UPTIME_PANELS: list[PanelSpec] = [
    PanelSpec(
        key="uptime.company",
        entity_type="company",
        title_key="uptime.panel.title",
        provider=company_uptime,
        position=460,
    ),
]
