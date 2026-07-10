"""REST endpoints for contacts under ``/api/v1/contacts`` (CLAUDE.md §6, §9).

Besides CRUD, this module owns the company↔contact link endpoints (nested under a contact,
since a module contributes a single router at the ``/contacts`` prefix).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.contacts.schemas import (
    ContactCreate,
    ContactLinkCreate,
    ContactLinkUpdate,
    ContactRead,
    ContactUpdate,
)
from app.modules.contacts.service import ContactService
from app.schemas import Page

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get(
    "",
    response_model=Page[ContactRead],
    dependencies=[require_permission("contacts.contact.read")],
)
async def list_contacts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    q: str | None = Query(None, max_length=200),
    sort: str | None = Query(
        None, description="first_name | last_name | email | job_title | company | …, '-' desc"
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[ContactRead]:
    items, total = await ContactService(ctx).list(
        limit=limit, offset=offset, company_id=company_id, q=q, sort=sort
    )
    return Page(
        items=[ContactRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ContactRead,
    status_code=201,
    dependencies=[require_permission("contacts.contact.write")],
)
async def create_contact(
    payload: ContactCreate,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    contact = await ContactService(ctx).create(payload)
    return ContactRead.model_validate(contact)


@router.get(
    "/{contact_id}",
    response_model=ContactRead,
    dependencies=[require_permission("contacts.contact.read")],
)
async def get_contact(
    contact_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    contact = await ContactService(ctx).get(contact_id)
    return ContactRead.model_validate(contact)


@router.patch(
    "/{contact_id}",
    response_model=ContactRead,
    dependencies=[require_permission("contacts.contact.write")],
)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    contact = await ContactService(ctx).update(contact_id, payload)
    return ContactRead.model_validate(contact)


@router.delete(
    "/{contact_id}",
    status_code=204,
    dependencies=[require_permission("contacts.contact.delete")],
)
async def delete_contact(
    contact_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ContactService(ctx).delete(contact_id)


# --- company links ---------------------------------------------------------- #
@router.post(
    "/{contact_id}/links",
    response_model=ContactRead,
    status_code=201,
    dependencies=[require_permission("contacts.link.write")],
)
async def link_contact_to_company(
    contact_id: uuid.UUID,
    payload: ContactLinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    service = ContactService(ctx)
    await service.link(
        contact_id, payload.company_id, is_primary=payload.is_primary or None
    )
    return ContactRead.model_validate(await service.get(contact_id))


@router.patch(
    "/{contact_id}/links/{company_id}",
    response_model=ContactRead,
    dependencies=[require_permission("contacts.link.write")],
)
async def update_contact_company_link(
    contact_id: uuid.UUID,
    company_id: uuid.UUID,
    payload: ContactLinkUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    service = ContactService(ctx)
    if payload.is_primary:
        await service.set_primary(contact_id, company_id)
    else:
        await service.link(contact_id, company_id, is_primary=False)
    return ContactRead.model_validate(await service.get(contact_id))


@router.delete(
    "/{contact_id}/links/{company_id}",
    status_code=204,
    dependencies=[require_permission("contacts.link.write")],
)
async def unlink_contact_from_company(
    contact_id: uuid.UUID,
    company_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ContactService(ctx).unlink(contact_id, company_id)
