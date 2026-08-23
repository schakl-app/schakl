"""What ``google_tag_manager`` contributes to a *client's* page. Business-licensed — see LICENSE.

This module used to draw its own card on the company hub. It no longer does (#411): three
integration cards — Ads, Tag Manager, Timeon — sat beside the marketing panel printing largely
the same facts one card lower down, and the team asked for one control and one place to read
them. What survives the removal is the one fact the card carried that nothing else did —
``workspace_changes``, a change staged weeks ago and never published — which now rides the
marketing panel's connections row through :mod:`app.core.tagmanager`.

**It reads stored rows and calls Google not at all.** A company page composes every enabled
module's provider in sequence with no per-provider try/except, so one slow or refusing
integration would hold — or break — the whole hub. The tags themselves stay one click away,
where waiting for Google is the point rather than a surprise.

The permission check lives *here*, not in the borrower: the marketing panel that reads this is
``explicit_public`` by design, and a contribution that trusts its caller to remember a
permission is #365's hope rather than #365's rule.
"""

from __future__ import annotations

import uuid

from app.core.tagmanager import CompanyContainer, register_container_provider
from app.core.tenancy import RequestContext
from app.integrations.google_tag_manager.service import GtmService, container_url


async def company_containers(
    ctx: RequestContext, company_id: uuid.UUID
) -> list[CompanyContainer]:
    """This client's active containers — **one** query, whatever the client's size."""
    if not ctx.can("google_tag_manager.container.read"):
        return []
    rows = await GtmService(ctx).list_containers(company_id=company_id, active_only=True)
    return [
        CompanyContainer(
            id=row.id,
            public_id=row.public_id or row.container_id,
            name=row.name or row.public_id or row.container_id,
            status=row.status,
            last_error=row.last_error,
            live_version_id=row.live_version_id,
            tag_count=row.tag_count or 0,
            workspace_changes=row.workspace_changes or 0,
            observed_at=row.observed_at,
            deep_link=container_url(row.account_id, row.container_id),
        )
        for row in rows
    ]


register_container_provider(company_containers)
