"""Pydantic schemas for the interactions module (contactmomenten)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.modules.interactions.models import (
    InteractionDirection,
    InteractionSource,
    InteractionStatus,
)


class InteractionKindDefBase(BaseModel):
    """A tenant-configurable interaction kind (#174) — the contact-types shape."""

    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    label_i18n: dict[str, str] = Field(default_factory=dict)
    position: int = 0
    active: bool = True


class InteractionKindDefCreate(InteractionKindDefBase):
    pass


class InteractionKindDefUpdate(BaseModel):
    label_i18n: dict[str, str] | None = None
    position: int | None = None
    active: bool | None = None


class InteractionKindDefRead(InteractionKindDefBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class Participant(BaseModel):
    email: EmailStr
    name: str | None = None
    role: str = Field("to", pattern="^(from|to|cc)$")


class ParticipantRead(Participant):
    """Read shape only (#160): the org contact this address resolves to, matched at read
    time so a contact created *after* the email was logged still links up."""

    contact_id: uuid.UUID | None = None
    #: The org member this address resolves to (#167) — a colleague, not a contact, so the
    #: web never offers to "create a contact" for them. Read-time match, like ``contact_id``.
    user_id: uuid.UUID | None = None


class InteractionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: A key into the org's kind list (#174) — no longer a closed enum.
    kind: str
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
    #: Labels of the linked records (#147), resolved in one batched query per table — the
    #: web draws link chips from these, never from a raw id or a 200-row lookup.
    company_name: str | None = None
    project_name: str | None = None
    task_title: str | None = None
    contact_name: str | None = None
    owner_user_id: uuid.UUID | None = None
    #: Resolved at read time: the live account wins, a departed one keeps its snapshot.
    owner_name: str | None = None
    owner_deleted: bool = False
    participants: list[ParticipantRead] = Field(default_factory=list)
    source: InteractionSource
    gmail_thread_id: str | None = None
    deep_link: str | None = None
    created_at: datetime


class InteractionLogTime(BaseModel):
    """The "Voeg aan mijn uren toe" ride-along (#175): a linked time entry created in the
    same transaction as the interaction. Times follow the *time* module's convention
    (wall-clock-as-UTC), unlike ``occurred_at`` — the entry must round-trip the timesheet."""

    started_at: datetime
    ended_at: datetime


class InteractionCreate(BaseModel):
    """A manually logged touchpoint — meetings, calls, notes. Emails only arrive via gmail."""

    #: Validated by the service against the org's active kinds; ``email`` is never manual.
    kind: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    occurred_at: datetime
    subject: str = Field(..., min_length=1, max_length=500)
    body_text: str | None = None
    direction: InteractionDirection = InteractionDirection.NONE
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    participants: list[Participant] = Field(default_factory=list)
    #: Optional: also log this touchpoint on my timesheet (#175).
    log_time: InteractionLogTime | None = None


class InteractionUpdate(BaseModel):
    kind: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
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


class InteractionApprove(BaseModel):
    """Approve a gmail row, optionally assigning it in the same step (#183) — the same link
    fields as a remap; an absent field leaves the row's current link untouched, ``null``
    clears it. Approving with no fields is the plain one-click approve."""

    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None


class InteractionReject(BaseModel):
    #: Also suppress the whole Gmail thread, so follow-ups never get logged either.
    suppress_thread: bool = False
