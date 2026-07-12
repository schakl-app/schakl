"""Hosting panel on the company detail view (issue #93, the modular hub — CLAUDE.md §6).

Lists the hosting entities attached to a client so the company page composes them via the
registry. Registered against ``entity_type="company"``.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.hosting.service import HostingService
from app.registry import PanelSpec


async def _hosting_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    hostings = await HostingService(ctx).hostings_for_company(company_id)
    return {
        "hosting": [
            {
                "id": str(h.id),
                "name": h.name,
                "provider_name": getattr(h, "provider_name", None),
                "ip_address": h.ip_address,
            }
            for h in hostings
        ],
    }


hosting_company_panel = PanelSpec(
    key="hosting.company",
    entity_type="company",
    title_key="hosting.panel.title",
    provider=_hosting_provider,
    position=50,
)
