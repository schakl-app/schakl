"""Contacts panel on the company detail view (CLAUDE.md §6, the modular hub).

Lists the contacts attached to a company. Registered against ``entity_type="company"`` so it
composes onto the company page with no edit to that page.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.contacts.models import Contact
from app.registry import PanelSpec


async def _contacts_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    repo = ctx.repo(Contact)
    contacts = await repo.list(limit=100, offset=0, company_id=company_id)
    return {
        "contacts": [
            {
                "id": str(c.id),
                "first_name": c.first_name,
                "last_name": c.last_name,
                "email": c.email,
                "phone": c.phone,
                "job_title": c.job_title,
            }
            for c in contacts
        ]
    }


contacts_company_panel = PanelSpec(
    key="contacts.company",
    entity_type="company",
    title_key="contacts.panel.title",
    provider=_contacts_provider,
    position=20,
)
