"""Pydantic schemas for the websites module (issue #94, CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.party.schemas import PartyReadRef, PartyRef


class WebsiteBase(BaseModel):
    root: bool = True
    technical_owner: PartyRef | None = None
    hosting_id: uuid.UUID | None = None
    uptime_enabled: bool = False
    custom: dict[str, Any] = Field(default_factory=dict)


class WebsiteCreate(WebsiteBase):
    domain_id: uuid.UUID


class WebsiteUpdate(BaseModel):
    root: bool | None = None
    technical_owner: PartyRef | None = None
    hosting_id: uuid.UUID | None = None
    uptime_enabled: bool | None = None
    custom: dict[str, Any] | None = None


class WebsiteRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    domain_id: uuid.UUID
    domain_name: str = ""
    root: bool
    technical_owner: PartyReadRef | None = None
    hosting_id: uuid.UUID | None = None
    hosting_name: str | None = None
    uptime_enabled: bool = False
    custom: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
