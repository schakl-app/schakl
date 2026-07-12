"""Pydantic schemas for stored files (issue #123)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StoredFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    filename: str
    content_type: str
    size_bytes: int
    entity_type: str | None
    entity_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
