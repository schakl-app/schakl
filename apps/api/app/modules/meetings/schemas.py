"""Pydantic shapes for the meetings module."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.ai.audio import MAX_ENCODED_CHARS
from app.modules.meetings.models import MeetingKind, MeetingSource, MeetingStatus


class MeetingParticipant(BaseModel):
    """One person in the room.

    Exactly one of ``user_id`` (a colleague) / ``contact_id`` (a contact of the client) may be
    set; neither means somebody known by name alone. ``speaker`` is the provider's label this
    person turned out to be (``S2``) — filled in after the transcript is back, and the one
    thing the review screen changes about a participant.
    """

    name: str = Field(min_length=1, max_length=255)
    user_id: uuid.UUID | None = None
    contact_id: uuid.UUID | None = None
    speaker: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _one_identity(self) -> MeetingParticipant:
        if self.user_id is not None and self.contact_id is not None:
            raise ValueError("meetings.error.participant_two_identities")
        return self


class MeetingCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    kind: MeetingKind = MeetingKind.PHYSICAL
    source: MeetingSource = MeetingSource.MICROPHONE
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    #: When the meeting took place; omitted means now (the recorder's case).
    occurred_at: dt.datetime | None = None
    #: The spoken language as a locale (``nl``, ``nl-NL``); omitted means the caller's own.
    language: str | None = Field(default=None, max_length=10)
    #: The person states they told the other participants the meeting is being recorded and
    #: what for. Refused when false — the API does not open a recording nobody was told about.
    participants_informed: bool = False
    #: Who is in the room, where the person knows it before pressing record. The recorder is
    #: added by the service when absent; the labels are filled in after transcription.
    participants: list[MeetingParticipant] = Field(default_factory=list, max_length=50)


class MeetingUpdate(BaseModel):
    """The definition fields — title, client, project, kind. The transcript, the speakers and
    the minutes have their own writes below."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    kind: MeetingKind | None = None
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    occurred_at: dt.datetime | None = None


class MeetingChunk(BaseModel):
    """One piece of the recording, base64 in JSON — the dictation's transport, one hop over.

    A browser's ``MediaRecorder`` with a ``timeslice`` hands out one blob a minute; only the
    first carries the container header, and byte-concatenating them in order is the whole file.
    So a piece is stored as it arrives and the worker folds them, which is what makes a crashed
    tab or a dead battery lose one minute rather than the meeting. An upload is the same route
    with the file cut into pieces of a few megabytes.
    """

    seq: int = Field(ge=0, le=100_000)
    audio: str = Field(min_length=1, max_length=MAX_ENCODED_CHARS)


class MeetingFinish(BaseModel):
    #: What the recorder counted, as the duration the pipeline plans by until a provider says
    #: better. Optional: an upload may not know.
    duration_seconds: int | None = Field(default=None, ge=0, le=24 * 3600)


class MeetingParticipants(BaseModel):
    """The whole roster, replaced: who was there and which speaker label each one is."""

    participants: list[MeetingParticipant] = Field(default_factory=list, max_length=50)


# --- the minutes ------------------------------------------------------------------------ #
class MinutesDecision(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    #: Where in the transcript it was said, and the words — the evidence a reviewer can check.
    at: float | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=500)
    #: ``False`` when the quote could not be found in the transcript: the item stays, marked.
    verified: bool = True


class MinutesActionItem(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=4000)
    #: A colleague from the org's staff shortlist — grounded, never guessed (#382).
    assignee_user_id: uuid.UUID | None = None
    #: A contact of the client who took it on — grounded in the meeting's participants. On
    #: confirm a ticked item becomes a task *assigned to that contact* (``assignee_contact_id``,
    #: #273), the "waiting on the client" shape, never a colleague's task wearing their name.
    owner_contact_id: uuid.UUID | None = None
    #: Who was named when it is neither: "Jan (leverancier)". Free text, display only.
    owner_label: str | None = Field(default=None, max_length=255)
    due_date: dt.date | None = None
    at: float | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=500)
    verified: bool = True
    #: The reviewer's decision to make a task of it. On by default for an item with an
    #: assignee among staff; a client's promise is recorded in the minutes, not on a board.
    create_task: bool = True


class MinutesTopic(BaseModel):
    heading: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=8000)


class MinutesDraft(BaseModel):
    """What the model drafted and the reviewer edits — the one shape on ``meetings.minutes``."""

    title: str | None = Field(default=None, max_length=255)
    summary: str = Field(default="", max_length=8000)
    topics: list[MinutesTopic] = Field(default_factory=list, max_length=40)
    decisions: list[MinutesDecision] = Field(default_factory=list, max_length=60)
    action_items: list[MinutesActionItem] = Field(default_factory=list, max_length=60)
    open_questions: list[str] = Field(default_factory=list, max_length=40)
    #: The model was cut off, or the transcript was too long to send whole: say so on screen.
    truncated: bool = False
    #: Only part of the transcript reached the model (an input cap): the minutes are partial.
    partial_input: bool = False


class MeetingConfirm(BaseModel):
    """The reviewer's final word: these minutes become a contact moment and these tasks."""

    minutes: MinutesDraft
    #: The interaction kind key the minutes land as, if not the meeting's own (#174: kinds are
    #: tenant-configurable, so a tenant may have a third).
    interaction_kind: str | None = Field(default=None, max_length=50, pattern=r"^[a-z0-9_]+$")


class MeetingRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    kind: MeetingKind
    source: MeetingSource
    status: MeetingStatus
    error_key: str | None = None
    occurred_at: dt.datetime
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    project_id: uuid.UUID | None = None
    project_name: str | None = None
    owner_user_id: uuid.UUID | None = None
    owner_name: str | None = None
    duration_seconds: int | None = None
    action_item_count: int = 0
    decision_count: int = 0
    created_at: dt.datetime


class MeetingList(BaseModel):
    items: list[MeetingRow]
    total: int | None = None


class TranscriptSegment(BaseModel):
    start: float
    end: float
    speaker: str | None = None
    text: str


class MeetingDetail(MeetingRow):
    language: str | None = None
    participants_informed_at: dt.datetime | None = None
    chunks_received: int = 0
    audio_file_id: uuid.UUID | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    transcript_text: str | None = None
    #: Which transcription produced the words, and in how many parts — a split recording keeps
    #: one set of speaker labels per part, which the screen says beside the labels.
    transcript_model: str | None = None
    transcript_parts: int = 0
    #: Whether the transcript labels speakers at all. ``False`` with a transcript means the
    #: speech model answered text only (``gpt-4o-transcribe``, ``gpt-transcribe``, whisper), and
    #: the screen says so by name rather than drawing an empty speaker list.
    diarized: bool = False
    participants: list[MeetingParticipant] = Field(default_factory=list)
    #: Derived from ``participants``: label → name, for the transcript's lines.
    speakers: dict[str, str] = Field(default_factory=dict)
    minutes: MinutesDraft | None = None
    interaction_id: uuid.UUID | None = None
    task_ids: list[uuid.UUID] = Field(default_factory=list)
    confirmed_at: dt.datetime | None = None
    #: The reviewer may write the draft and confirm it: the two keys the screen mirrors.
    can_write: bool = False
    can_delete: bool = False


class MeetingStatusRead(BaseModel):
    """The one column the detail page polls while a worker holds the row."""

    status: MeetingStatus
    error_key: str | None = None
    status_at: dt.datetime


class MeetingConfirmResult(BaseModel):
    interaction_id: uuid.UUID
    task_ids: list[uuid.UUID]
    #: Action items the reviewer ticked that could not become a task, with the field the
    #: refusal named — reported, never raised, so the minutes still land (§18's split).
    skipped: list[dict[str, Any]] = Field(default_factory=list)
