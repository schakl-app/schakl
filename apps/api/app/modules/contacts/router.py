"""REST endpoints for contacts under ``/api/v1/contacts`` (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.tenancy import RequestContext, require_context
from app.modules.contacts.schemas import ContactCreate, ContactRead, ContactUpdate
from app.modules.contacts.service import ContactService
from app.schemas import Page

router = APIRouter(prefix="/contacts", tags=["contacts"])


@router.get("", response_model=Page[ContactRead])
async def list_contacts(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> Page[ContactRead]:
    items, total = await ContactService(ctx).list(
        limit=limit, offset=offset, company_id=company_id
    )
    return Page(
        items=[ContactRead.model_validate(c) for c in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=ContactRead, status_code=201)
async def create_contact(
    payload: ContactCreate,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    contact = await ContactService(ctx).create(payload)
    return ContactRead.model_validate(contact)


@router.get("/{contact_id}", response_model=ContactRead)
async def get_contact(
    contact_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    contact = await ContactService(ctx).get(contact_id)
    return ContactRead.model_validate(contact)


@router.patch("/{contact_id}", response_model=ContactRead)
async def update_contact(
    contact_id: uuid.UUID,
    payload: ContactUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ContactRead:
    contact = await ContactService(ctx).update(contact_id, payload)
    return ContactRead.model_validate(contact)


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ContactService(ctx).delete(contact_id)
