"""Pydantic schemas for the hosting module (issue #93, CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.party.schemas import PartyReadRef, PartyRef


class HostingBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    company_id: uuid.UUID | None = None
    provider_id: uuid.UUID | None = None
    ip_address: str | None = Field(default=None, max_length=45)
    contact: PartyRef | None = None
    custom: dict[str, Any] = Field(default_factory=dict)


class HostingCreate(HostingBase):
    pass


class HostingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    company_id: uuid.UUID | None = None
    provider_id: uuid.UUID | None = None
    ip_address: str | None = Field(default=None, max_length=45)
    contact: PartyRef | None = None
    custom: dict[str, Any] | None = None


class HostingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    provider_id: uuid.UUID | None = None
    provider_name: str | None = None
    ip_address: str | None = None
    contact: PartyReadRef | None = None
    custom: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
