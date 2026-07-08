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


class ContactCompanyLink(BaseModel):
    """A company a contact is attached to, with the per-company primary flag."""

    model_config = ConfigDict(from_attributes=True)

    company_id: uuid.UUID
    name: str
    is_primary: bool


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
    """Attach a contact to a company; optionally make it that company's primary."""

    company_id: uuid.UUID
    is_primary: bool = False


class ContactLinkUpdate(BaseModel):
    """Update a company↔contact link (only the primary flag is mutable)."""

    is_primary: bool
