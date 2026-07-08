"""Pydantic schemas for the contacts module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ContactBase(BaseModel):
    company_id: uuid.UUID | None = None
    first_name: str = Field(min_length=1, max_length=255)
    last_name: str | None = Field(default=None, max_length=255)
    email: str | None = Field(default=None, max_length=320)
    phone: str | None = Field(default=None, max_length=64)
    job_title: str | None = Field(default=None, max_length=255)
    notes: str | None = None
    # Per-tenant custom values, validated against tenant definitions for entity_type "contact".
    custom: dict[str, Any] = Field(default_factory=dict)


class ContactCreate(ContactBase):
    pass


class ContactUpdate(BaseModel):
    company_id: uuid.UUID | None = None
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
