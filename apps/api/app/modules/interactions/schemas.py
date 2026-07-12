"""Pydantic schemas for the interactions module (contactmomenten)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.interactions.models import (
    InteractionDirection,
    InteractionKind,
    InteractionSource,
    InteractionStatus,
)


class Participant(BaseModel):
    email: EmailStr
    name: str | None = None
    role: str = Field("to", pattern="^(from|to|cc)$")


class InteractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: InteractionKind
    status: InteractionStatus
    occurred_at: datetime
    subject: str | None = None
    snippet: str | None = None
    #: For gmail rows this stays ``None`` until the mailbox owner approves.
    body_text: str | None = None
    direction: InteractionDirection
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    #: Resolved at read time: the live account wins, a departed one keeps its snapshot.
    owner_name: str | None = None
    owner_deleted: bool = False
    participants: list[Participant] = Field(default_factory=list)
    source: InteractionSource
    gmail_thread_id: str | None = None
    deep_link: str | None = None
    created_at: datetime


class InteractionCreate(BaseModel):
    """A manually logged touchpoint — meetings, calls, notes. Emails only arrive via gmail."""

    kind: InteractionKind
    occurred_at: datetime
    subject: str = Field(..., min_length=1, max_length=500)
    body_text: str | None = None
    direction: InteractionDirection = InteractionDirection.NONE
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    participants: list[Participant] = Field(default_factory=list)


class InteractionUpdate(BaseModel):
    kind: InteractionKind | None = None
    occurred_at: datetime | None = None
    subject: str | None = Field(None, min_length=1, max_length=500)
    body_text: str | None = None
    direction: InteractionDirection | None = None
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    participants: list[Participant] | None = None


class InteractionRemap(BaseModel):
    """Move a gmail-sourced interaction — only fields actually sent change; ``null`` clears."""

    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None


class InteractionReject(BaseModel):
    #: Also suppress the whole Gmail thread, so follow-ups never get logged either.
    suppress_thread: bool = False
