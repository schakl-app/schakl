"""Contacts panel on the company detail view (CLAUDE.md §6, the modular hub).

Powers the client page's contact-person field. Returns, self-contained, everything the field
needs so the company page stays generic:
  * ``contacts``   — linked contacts, primary-first, each with ``is_primary``;
  * ``candidates`` — attachable org contacts (the type-ahead's options);
  * ``definitions``— the tenant's ``contact`` custom-field definitions for the "＋ add" modal.
Registered against ``entity_type="company"`` so it composes onto the company page.
"""

from __future__ import annotations

import uuid

from app.core.customfields import CustomFieldsService
from app.core.customfields.schemas import CustomFieldDefinitionRead
from app.core.tenancy import RequestContext
from app.modules.contacts.service import ContactService
from app.registry import PanelSpec


async def _contacts_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    service = ContactService(ctx)
    linked = await service.contacts_for_company(company_id)
    candidates = await service.candidates_for_company(company_id)
    definitions = await CustomFieldsService(ctx).definitions("contact")

    return {
        "contacts": [
            {
                "id": str(contact.id),
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email,
                "phone": contact.phone,
                "job_title": contact.job_title,
                "is_primary": is_primary,
            }
            for contact, is_primary in linked
        ],
        "candidates": [
            {
                "id": str(contact.id),
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email,
                "job_title": contact.job_title,
            }
            for contact in candidates
        ],
        "definitions": [
            CustomFieldDefinitionRead.model_validate(d).model_dump(mode="json")
            for d in definitions
        ],
    }


contacts_company_panel = PanelSpec(
    key="contacts.company",
    entity_type="company",
    title_key="contacts.panel.title",
    provider=_contacts_provider,
    position=20,
)
