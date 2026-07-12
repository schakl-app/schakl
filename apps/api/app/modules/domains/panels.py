"""Domains panel on the company detail view (issue #90, the modular hub — CLAUDE.md §6).

Lists a client's domains (name, status, whether email is on) so the company page composes them
via the registry with no edits to that page. Registered against ``entity_type="company"``.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.domains.service import DomainService
from app.registry import PanelSpec


async def _domains_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    domains = await DomainService(ctx).domains_for_company(company_id)
    return {
        "domains": [
            {
                "id": str(d.id),
                "name": d.name,
                "status": d.status,
                "email_enabled": d.email_enabled,
            }
            for d in domains
        ],
    }


domains_company_panel = PanelSpec(
    key="domains.company",
    entity_type="company",
    title_key="domains.panel.title",
    provider=_domains_provider,
    position=40,
)
