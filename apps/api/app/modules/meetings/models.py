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
  transcribing → summarising → ``ready`` (the minutes are in) — or ``failed``, with the reason
  as an i18n key. The worker owns the two middle states; ``status_at`` is what the reaper reads.
- **The minutes are the record, and they are never frozen.** ``minutes`` holds what the model
  drafted and every edit a colleague made since; there is no confirm step (there was one, and
  it was removed: a meeting whose minutes cannot be corrected after the fact is a meeting whose
  minutes are wrong the moment somebody spots a typo, and "only after confirming" is a stage
  nobody asked for). The moment the draft lands the meeting is *filed* — a contact moment on the
  client, written as the recorder through the interactions module's own service, and rewritten
  on every edit so the timeline and the meeting cannot disagree. A task is made from one action
  item at a time, checked by a person in the task sheet, never by a checkbox on confirm.
- **The owner is snapshotted** (``owner_name``, #64): the colleague who recorded it keeps their
  name on the minutes after they leave.
- **The roster is a list of people, not a map of labels.** ``participants`` names who was there
  — a colleague by ``user_id``, a client's contact by ``contact_id``, anyone else by name — and
  a provider's speaker label (``S2``) is a *property of a participant*, filled in once the
  transcript is back. The other way round (``speakers = {"S2": "Jan"}``, the first shape) could
  say who a label was and never who was in the room, so an action item had nobody to be
  grounded in and a client's promise could not become a task assigned to that client.
- **``participants_informed_at`` is a statement, not a checkbox.** Recording a conversation you
  take part in is legal here; *not telling the others* is not (AVG art. 13, and Sr 139a/b for a
  secret one), so the API refuses to open a recording until the person stated they did.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    text,
)
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
    #: The minutes are in. Editable for ever; a redraft or a retry passes through ``queued``
    #: and lands here again. (``review`` and ``done`` collapsed into this in migration
    #: ``b7d4f2c9a1e6``: the confirm step between them was removed.)
    READY = "ready"
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

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """The rule an **external (client) login** reads meetings by (§15, #266): none.

        The column-matched horizon would hand a client every meeting on their own companies,
        *and* every meeting attached to no company at all (a NULL is "not company data" for
        staff). Neither is theirs: a transcript is a verbatim record of what the agency's people
        said in the room, and a draft is prose a model wrote that nobody has confirmed yet
        (``docs/MEETINGS.md``). What a client is owed is the *confirmed* contact moment, which
        the interactions module already serves under its own rules.

        It lives on the model so every path answers the same — the list and its total, the
        detail, the polled status, the company panel, and the two reference seams
        (``entity_visible``, which gates the recording's bytes now that ``meeting`` is a
        record-gated file host, and ``app/core/directory.py``). Stated as a clause rather
        than as an ``is_portal`` refusal in each read, because a refusal in seven places is
        the #285 shape: one of them forgets.
        """
        return false()

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The title was generated because the recorder left the box empty ("Bespreking met Nova
    #: Fietsen · 23-09-2026"). While true, the minutes may name the meeting once the words are
    #: in; a title a person typed or edited is theirs and is never replaced.
    title_auto: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
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
    #: The first shape of the roster — the reviewer's names for the provider's labels,
    #: ``{"S1": "Jan de Vries"}``. Kept for the rows already written; read as a name-only roster
    #: where ``participants`` is ``NULL``, never written any more.
    speakers: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    #: Who was in the room: ``[{"name", "user_id", "contact_id", "speaker"}]`` — a colleague
    #: (``user_id``), a contact of the client (``contact_id``) or somebody with only a name, each
    #: optionally holding the provider's speaker label they turned out to be. Stated *before* the
    #: recording where the person knows it, corrected in review, and what the minutes' "who took
    #: this on" is grounded in (``minutes.py``).
    participants: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    #: The drafted minutes and the reviewer's edits to them (``minutes.MinutesDraft``).
    minutes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # What the meeting produced on other modules' tables.
    #: The contact moment the minutes are filed as — written when the draft lands and kept in
    #: step with every edit (``service.MeetingService.sync_interaction``). ``SET NULL`` when
    #: somebody deletes the moment; the next edit files it again.
    interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("interactions.id", ondelete="SET NULL"), nullable=True
    )
    #: The tasks made from action items, as ids — a list, because a meeting produces several
    #: and the ids are all the detail page needs to link them.
    task_ids: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    #: The time entries booked for the colleagues at the table (``log_time``), as ids — so the
    #: page can say *whose* hours were written and link them, because hours nobody asked to see
    #: written are a surprise on a timesheet.
    time_entry_ids: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    #: When the old confirm step ran, on rows that predate its removal. No longer written, kept
    #: for one release (expand/contract) and read by nothing.
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


#: The sections a minutes document can carry, in print order. ``transcript`` is the one that
#: is off unless asked for: it is the longest thing on the record and the one a reader of the
#: minutes least often wants on paper.
DOCUMENT_SECTIONS: tuple[str, ...] = (
    "participants",
    "summary",
    "topics",
    "decisions",
    "action_items",
    "open_questions",
    "evidence",
    "transcript",
)
DEFAULT_DOCUMENT_SECTIONS: tuple[str, ...] = (
    "participants",
    "summary",
    "topics",
    "decisions",
    "action_items",
    "open_questions",
)


class MeetingSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Org-wide meetings settings (one row per org, absent = the defaults).

    Three things live here. **Whether the recorder asks for the consent statement**
    (``consent_required``): on by default — the API refuses to open a recording nobody was told
    about — and off for an agency whose own procedure already covers it, so the checkbox and
    the refusal go together. **What the minutes document looks like** (``document_*``): the
    design, the accent, the cover, the closing line, which sections a download ticks by default,
    and a tenant's own Jinja where they bring one — the reporting template's shape, one row
    rather than a library, because a meeting has one audience. And **the agency's own writing
    instructions for the minutes** (``ai_instructions``): the editorial half of the prompt is
    the tenant's, exactly as a report tone is (#300), and it reaches the model inside the
    system prompt's rules block — a house rule, never a fact about one meeting.
    """

    __tablename__ = "meeting_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_meeting_settings_org"),)

    consent_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    document_design: Mapped[str] = mapped_column(
        String(32), nullable=False, default="standard", server_default="standard"
    )
    #: Overrides ``org_settings.primary_color`` for this document family only. NULL = brand.
    document_accent_color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: A stored file, never a URL: the renderer's fetcher answers ``data:`` and nothing else.
    document_cover_file_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("files.id", ondelete="SET NULL"), nullable=True
    )
    document_footer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Which sections a download ticks before the person changes anything.
    document_sections: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    #: Whether a participant's profile picture is drawn beside their name where one is known.
    document_avatars: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    document_custom_html: Mapped[str | None] = mapped_column(Text, nullable=True)
    document_custom_css: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_instructions: Mapped[str | None] = mapped_column(Text, nullable=True)
