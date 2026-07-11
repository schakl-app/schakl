"""Activity feed response schema (issue #67)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ActivityItem(BaseModel):
    """One line of a record's paper trail."""

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    #: Maps to an ``activity.action.*`` i18n key (``created``, ``updated``, …).
    action: str
    #: Display name at read time; ``None`` means the system acted.
    actor_name: str | None = None
    #: True when the actor's account was deleted — the name is a snapshot (issue #64).
    actor_deleted: bool = False
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
