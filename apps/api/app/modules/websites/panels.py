"""Websites panel on the company detail view (owner request, the modular hub — CLAUDE.md §6).

Lists a client's websites — the ones whose parent domain belongs to the company — so the
company page composes them via the registry. This replaces the hosting panel there: hosting
is shared infrastructure managed under Instellingen, while the websites are the client's.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.websites.service import WebsiteService
from app.registry import PanelSpec

#: How many websites the client card shows before handing over to the list — the domains
#: panel's number and rule (``domains/panels.py``): the panel is the first page of the list it
#: links to, same filter and same default sort, so "Alle 12 bekijken" continues where the card
#: stopped instead of reshuffling.
_PANEL_LIMIT = 5


async def _websites_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    websites, total = await WebsiteService(ctx).list(
        limit=_PANEL_LIMIT, offset=0, company_id=company_id
    )
    return {
        # The whole count, never the shown one: five over a client who has twelve reads as the
        # complete answer, which is the one thing a summary must not do.
        "total": total,
        "websites": [
            {
                "id": str(w.id),
                "domain_id": str(w.domain_id),
                "name": getattr(w, "domain_name", ""),
                "root": w.root,
                "hosting_name": getattr(w, "hosting_name", None),
                "uptime_enabled": w.uptime_enabled,
            }
            for w in websites
        ],
    }


websites_company_panel = PanelSpec(
    key="websites.company",
    entity_type="company",
    title_key="websites.panel.title",
    provider=_websites_provider,
    # Rarely-consulted asset panel: near the bottom with domains, before only the trail.
    position=70,
)
