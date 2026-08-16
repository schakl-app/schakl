"""The Tag Manager panel on a client's page. Business-licensed — see LICENSE.

**It reads stored rows and calls Google not at all.** A company page composes every enabled
module's panel in sequence with no per-panel try/except, so one slow or refusing integration would
hold — or break — the whole hub. What it shows is what schakl already knows: which containers this
client has, what is live in each, whether anything is staged and unpublished, and whether the
container still answers. The tags themselves are one click away, where waiting for Google is the
point rather than a surprise.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.integrations.google_tag_manager.service import GtmService, container_url
from app.registry import SIZE_HALF, PanelSpec


async def _provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("google_tag_manager.container.read"):
        # Quiet rather than an error: the panel is permission-gated, and a card reading "no
        # access" on a page full of working cards teaches nobody anything.
        return {"forbidden": True}
    service = GtmService(ctx)
    containers = await service.list_containers(company_id=company_id, active_only=True)
    # One grouped read for every container's conversion counts, not one read per container: a
    # panel that is two queries for this client and thirty for the next is the shape a functional
    # test cannot see (docs/PERFORMANCE.md).
    counts = await service.conversion_counts([row.id for row in containers])
    rows = []
    for row in containers:
        total, live = counts.get(row.id, (0, 0))
        rows.append(
            {
                "id": str(row.id),
                "public_id": row.public_id or row.container_id,
                "name": row.name or row.public_id or row.container_id,
                "status": row.status,
                # Google's own sentence, already scrubbed. Shown as-is: it is the one thing that
                # says *what* to fix, and translating it would mean inventing categories Google
                # does not have.
                "last_error": row.last_error,
                "live_version_id": row.live_version_id,
                "tag_count": row.tag_count,
                # The number an agency wants to notice without opening anything: a change staged
                # weeks ago and never published is the commonest way tracking quietly stops
                # being what the client was told it is.
                "workspace_changes": row.workspace_changes,
                "conversions": total,
                "conversions_live": live,
                "observed_at": row.observed_at.isoformat() if row.observed_at else None,
                "tag_manager_url": container_url(row.account_id, row.container_id),
            }
        )
    return {"containers": rows, "can_manage": ctx.can("google_tag_manager.settings.manage")}


gtm_company_panel = PanelSpec(
    key="google_tag_manager.company",
    entity_type="company",
    title_key="gtm.panel.title",
    provider=_provider,
    # Directly under Google Ads (51), because "what is measuring the site" and "what is being
    # spent on it" are read together.
    position=52,
    requires_permission="google_tag_manager.container.read",
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("containers"),
)
