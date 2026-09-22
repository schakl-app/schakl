"""``meetings`` — a recorded meeting, its transcript and the minutes drafted from it.

One org-scoped row per meeting. The **audio is a file** (``files`` rows, ``entity_type =
"meeting"``): the parts the browser uploads while it records ride as body content
(``content_id = "chunk:<seq>"``, hidden from every attachment list) and are folded by the worker
into the one recording the row names in ``audio_file_id``. The **transcript and the minutes are
JSONB on the row**: they are read whole or not at all, they are never filtered on, and a
document that is one record is what makes "regenerate", "delete the audio but keep the words"
and the review screen simple.

Shape decisions:

- **A meeting is a record with a lifecycle** (``MeetingStatus``): recorded → queued →
  transcribing → summarising → ``review`` (a person reads the draft) → ``done`` (the minutes are
  a contact moment and the action items are tasks) — or ``failed``, with the reason as an i18n
  key. The worker owns the two middle states; ``status_at`` is what the reaper reads.
- **Nothing the model wrote is a record until a person confirms it.** ``minutes`` holds the
  draft (and the reviewer's edits to it); the interaction and the tasks are written on confirm,
  through their own modules' services, as the reviewer. That is the dictation posture (#382),
  not the e-mail one (#327): a colleague pressed record and a colleague presses confirm.
- **The owner is snapshotted** (``owner_name``, #64): the colleague who recorded it keeps their
  name on the minutes after they leave.
- **``participants_informed_at`` is a statement, not a checkbox.** Recording a conversation you
  take part in is legal here; *not telling the others* is not (AVG art. 13, and Sr 139a/b for a
  secret one), so the API refuses to open a recording until the person stated they did.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

ENTITY_TYPE = "meeting"


class MeetingStatus(StrEnum):
    RECORDING = "recording"
    QUEUED = "queued"
    TRANSCRIBING = "transcribing"
    SUMMARISING = "summarising"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"


#: The states a worker holds — a row in one of these with a stale ``status_at`` is a run whose
#: worker is gone (the #300 rule; ``jobs.meetings_reap_stale``).
WORKER_STATUSES = frozenset({MeetingStatus.TRANSCRIBING.value, MeetingStatus.SUMMARISING.value})


class MeetingKind(StrEnum):
    """Which contact-moment kind the minutes land as — the interactions module's own keys."""

    PHYSICAL = "physical"
    ONLINE = "online"


class MeetingSource(StrEnum):
    MICROPHONE = "microphone"
    #: A browser tab's audio (a Meet, Teams or Zoom call in the browser) mixed with the mic.
    TAB = "tab"
    #: A file somebody already had — a phone's voice memo, a download.
    UPLOAD = "upload"


class Meeting(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "meetings"
    __entity_type__ = ENTITY_TYPE
    __activity_read_permission__ = "meetings.meeting.read"
    __table_args__ = (
        Index("ix_meetings_org_occurred", "org_id", "occurred_at"),
        Index("ix_meetings_org_status", "org_id", "status"),
    )

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MeetingKind.PHYSICAL.value
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=MeetingStatus.RECORDING.value
    )
    status_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Why a ``failed`` row failed — an i18n key, never a provider's prose (§9).
    error_key: Mapped[str | None] = mapped_column(String(120), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The spoken language, as a locale prefix (``nl``); the recogniser's hint.
    language: Mapped[str | None] = mapped_column(String(10), nullable=True)

    # Links. ``SET NULL`` like an interaction's: a deleted project must not erase the record
    # that a meeting happened.
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    participants_informed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The recording.
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    chunks_received: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    #: The container the first chunk sniffed as (``webm``, ``m4a``…); every later chunk is a
    #: continuation of it and carries no header of its own.
    audio_format: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: The folded recording. ``NULL`` before the worker folds the chunks, and again once the
    #: retention sweep has dropped the audio — the transcript and the minutes stay.
    audio_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )

    # The words. ``transcript`` is ``{"segments": [{start, end, speaker, text}], "model": …,
    # "parts": n}``; ``transcript_text`` the same words flat, for the search box and the prompt.
    transcript: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The reviewer's names for the provider's speaker labels: ``{"S1": "Jan de Vries"}``.
    speakers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: The drafted minutes and the reviewer's edits to them (``minutes.MinutesDraft``).
    minutes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # What confirming produced.
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("interactions.id", ondelete="SET NULL"), nullable=True
    )
    #: The tasks confirm created, as ids — a list, because a meeting produces several and the
    #: ids are all the detail page needs to link them.
    task_ids: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
