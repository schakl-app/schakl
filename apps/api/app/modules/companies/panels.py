"""Company-detail panels this module contributes (CLAUDE.md §6, the modular hub).

A panel is a (title + async data provider) the company detail view composes. Future modules
(contacts, websites, hosting, …) attach their own panels to ``entity_type="company"`` the same
way, with no change to the company page.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.registry import PanelSpec


async def _details_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    company = await ctx.repo(Company).get_or_404(company_id)
    return {
        "name": company.name,
        "website": company.website,
        "invoice_email": company.invoice_email,
        # Billing identity (issue #11): what invoicing (#207) snapshots at issue.
        "vat_number": company.vat_number,
        "coc_number": company.coc_number,
        "address_line1": company.address_line1,
        "address_line2": company.address_line2,
        "postal_code": company.postal_code,
        "city": company.city,
        "country": company.country,
        "notes": company.notes,
        "custom": company.custom,
    }


company_details_panel = PanelSpec(
    key="companies.details",
    entity_type="company",
    title_key="companies.panel.details",
    provider=_details_provider,
    position=10,
)
