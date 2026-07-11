"""Pydantic schemas for the provider catalog (issue #89, CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.core.providers.models import ProviderKind


class ProviderBase(BaseModel):
    kind: ProviderKind
    name: str = Field(min_length=1, max_length=255)
    config: dict[str, Any] = Field(default_factory=dict)
    active: bool = True
    position: int = 0


class ProviderCreate(ProviderBase):
    pass


class ProviderUpdate(BaseModel):
    # ``kind`` is immutable: a provider referenced as a registrar must not silently become an
    # email host underneath the domains pointing at it. Recreate instead.
    name: str | None = Field(default=None, min_length=1, max_length=255)
    config: dict[str, Any] | None = None
    active: bool | None = None
    position: int | None = None


class ProviderRead(ProviderBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
