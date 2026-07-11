"""Pydantic schemas for the contacts module (CLAUDE.md §6, §9).

A contact links to **many** companies via ``company_contacts``. ``ContactCreate`` accepts
``company_ids`` to attach on creation; ``ContactRead`` returns the linked ``companies`` with
their per-company ``is_primary`` flag. Link mutations use the small ``ContactLink*`` bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContactTypeBase(BaseModel):
    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    label_i18n: dict[str, str] = Field(default_factory=dict)
    position: int = 0
    active: bool = True


class ContactTypeCreate(ContactTypeBase):
    pass


class ContactTypeUpdate(BaseModel):
    label_i18n: dict[str, str] | None = None
    position: int | None = None
    active: bool | None = None


class ContactTypeRead(ContactTypeBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class ContactCompanyLink(BaseModel):
    """A company a contact is attached to, with the per-company primary flag and contact type."""

    model_config = ConfigDict(from_attributes=True)

    company_id: uuid.UUID
    name: str
    is_primary: bool
    contact_type_id: uuid.UUID | None = None


class ContactBase(BaseModel):
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    job_title: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    # Per-tenant custom values, validated against tenant definitions for entity_type "contact".
    custom: dict[str, Any] = Field(default_factory=dict)


class ContactCreate(ContactBase):
    # Companies to attach on creation; the first contact of a company becomes its primary.
    company_ids: list[uuid.UUID] = Field(default_factory=list)


class ContactUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    job_title: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    custom: dict[str, Any] | None = None


class ContactRead(ContactBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Companies this contact is attached to (primary-first).
    companies: list[ContactCompanyLink] = Field(default_factory=list)


class ContactLinkCreate(BaseModel):
    """Attach a contact to a company; optionally make it that company's primary and type it."""

    company_id: uuid.UUID
    is_primary: bool = False
    contact_type_id: uuid.UUID | None = None


class ContactLinkUpdate(BaseModel):
    """Update a company↔contact link (the primary flag and/or the contact type)."""

    is_primary: bool = False
    # Present ⇒ set the link's type (``null`` clears it). Absent ⇒ leave the type unchanged.
    contact_type_id: uuid.UUID | None = None
