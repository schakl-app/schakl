"""Wire shapes of the trash can (docs/TRASH.md)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class TrashDependentCount(BaseModel):
    """One contributing module's answer: how many of *its* rows hang off this record."""

    key: str
    #: The web prints ``tn(label_key, count)``; the API picks no locale for anyone (§8).
    label_key: str
    count: int


class TrashPreview(BaseModel):
    """What deleting a **live** record would do — read by the confirmation dialog before it asks.

    ``blocking`` non-empty means the delete is refused (``errors.trash_blocked``) and the dialog
    should offer the record's own gentler lifecycle instead; ``taken_along`` is what hides with
    the record and goes when it is purged.
    """

    entity_type: str
    entity_id: uuid.UUID
    can_trash: bool
    blocking: list[TrashDependentCount] = Field(default_factory=list)
    taken_along: list[TrashDependentCount] = Field(default_factory=list)
    retention_days: int


class TrashItem(BaseModel):
    """One row in the trash."""

    entity_type: str
    entity_id: uuid.UUID
    label: str
    deleted_at: datetime
    deleted_by_user_id: uuid.UUID | None = None
    deleted_by_name: str | None = None
    #: When the nightly sweep will purge it — unless something blocks that, in which case the
    #: row says so and stays until somebody restores it.
    purge_at: datetime
    blocking: list[TrashDependentCount] = Field(default_factory=list)
    taken_along: list[TrashDependentCount] = Field(default_factory=list)


class TrashPage(BaseModel):
    items: list[TrashItem]
    total: int
    retention_days: int
