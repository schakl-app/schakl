"""Pydantic schemas for API keys and service accounts (#20)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServiceAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class ServiceAccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    active: bool
    created_by_user_id: uuid.UUID | None
    created_at: datetime


class ApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    #: Permission strings the key may exercise. Capped by the creator; for a personal key also by
    #: the owner's live permissions on every request. Empty = a read of nothing useful, so at
    #: least one is required.
    scopes: list[str] = Field(min_length=1)
    #: Required (#20). The service caps it at a maximum and rejects a past date.
    expires_at: datetime


class ServiceAccountKeyCreate(ApiKeyCreate):
    service_account_id: uuid.UUID


class ApiKeyRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    #: ``schakl_<prefix>_****`` — never the secret (#20).
    redacted: str
    principal_type: str
    user_id: uuid.UUID | None
    service_account_id: uuid.UUID | None
    scopes: list[str]
    expires_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class ApiKeyCreated(ApiKeyRead):
    """The one and only time the full secret is returned — at creation."""

    secret: str
