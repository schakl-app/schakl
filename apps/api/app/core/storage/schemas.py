"""Pydantic schemas for stored files (issue #123)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class StoredFileRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    # Which backend holds the bytes (#190) and under which opaque key (`<org_id>/<file_id>`,
    # no secret) — surfaced for ops: "which rows still live on the volume?" is answerable.
    backend: str
    storage_key: str
    filename: str
    content_type: str
    size_bytes: int
    entity_type: str | None
    entity_id: uuid.UUID | None
    # Set means the file is part of its entity's *body* rather than attached to it — an
    # e-mail's `cid:` image. Listed only when explicitly asked for.
    content_id: str | None = None
    created_by_user_id: uuid.UUID | None
    created_at: datetime
