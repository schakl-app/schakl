"""Pydantic shapes for the meetings module."""

from __future__ import annotations

import datetime as dt
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.ai.audio import MAX_ENCODED_CHARS
from app.core.ai.schemas import TimeTranscribeRequest
from app.modules.meetings.models import (
    DEFAULT_DOCUMENT_SECTIONS,
    DOCUMENT_SECTIONS,
    MeetingKind,
    MeetingSource,
    MeetingStatus,
)


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
    #: Optional: left blank, the service names the meeting after its kind, client and date
    #: ("Bespreking met Nova Fietsen · 23-09-2026") and marks the row ``title_auto``, so the
    #: minutes may name it properly once the words are in. A title the person typed is theirs.
    title: str | None = Field(default=None, max_length=255)
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
    #: Which recorder session the piece belongs to. ``0`` is the one the recording started
    #: with; every time the capture was lost and taken up again (a locked phone, an incoming
    #: call) the recorder starts a new ``MediaRecorder`` and counts this up, because that
    #: one writes a container header of its own and its pieces cannot be byte-appended to
    #: the first session's. ``seq`` keeps counting across sessions.
    session: int = Field(default=0, ge=0, le=999)
    #: The first piece of a later session — the one carrying the header. Sniffed like ``seq``
    #: 0 and held to the same container.
    head: bool = False

    @model_validator(mode="after")
    def _head_is_a_later_session(self) -> MeetingChunk:
        if self.head and self.session == 0:
            raise ValueError("head marks the first piece of a session after the first")
        return self


class RecordingGap(BaseModel):
    """Where the recording was interrupted: at which second of the (joined) recording, and
    for how long nothing was captured."""

    at: float = Field(ge=0)
    seconds: float = Field(ge=0)


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
    #: A contact of the client who took it on — grounded in the meeting's participants. A task
    #: made of such an item is *assigned to that contact* (``assignee_contact_id``, #273), the
    #: "waiting on the client" shape, never a colleague's task wearing their name.
    owner_contact_id: uuid.UUID | None = None
    #: Who was named when it is neither: "Jan (leverancier)". Free text, display only.
    owner_label: str | None = Field(default=None, max_length=255)
    due_date: dt.date | None = None
    at: float | None = Field(default=None, ge=0)
    quote: str | None = Field(default=None, max_length=500)
    verified: bool = True
    #: The task this item became — made from the page with schakl's draft, one item at a time
    #: (``POST /meetings/{id}/action-items/task``), and filed on the contact moment. The only
    #: way an action item becomes a task: a stored ``create_task`` from before the confirm step
    #: was removed is ignored on read.
    task_id: uuid.UUID | None = None


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
    #: One line for a timesheet — what this meeting was, in the words a colleague would type
    #: beside the hours ("Kick-off homepage met Nova: planning en teksten"). ``log_time`` uses
    #: it as every entry's description unless the person types another.
    time_note: str | None = Field(default=None, max_length=200)
    #: The model was cut off, or the transcript was too long to send whole: say so on screen.
    truncated: bool = False
    #: Only part of the transcript reached the model (an input cap): the minutes are partial.
    partial_input: bool = False


class MeetingLogTime(BaseModel):
    """ "Uren registreren" for a meeting (#175's ride-along, #314's gates): one time entry per
    colleague named, for the meeting's duration, filed on the contact moment. The dialog shows
    every entry it will write — who, how long, the line beside it — before the press, because
    hours written for a colleague are on *their* timesheet.
    """

    #: The colleagues to book — staff ids, each one a participant or the reviewer themself.
    #: Booking somebody else asks ``time.entry.write:any``; one's own hours ``:own``.
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    #: How long, in minutes. Omitted means the recording's own length.
    minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    #: The entry's description; blank means the minutes' ``time_note``, then the title.
    description: str | None = Field(default=None, max_length=2000)
    #: Left out defers to the project (#284), exactly as the entry form does.
    billable: bool | None = None


class MeetingTimeEntry(BaseModel):
    """One booked entry, as the page says it: whose, how long, and the row to open."""

    id: uuid.UUID
    user_id: uuid.UUID
    user_name: str
    minutes: int
    date: dt.date


class MeetingRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    kind: MeetingKind
    source: MeetingSource
    status: MeetingStatus
    error_key: str | None = None
    occurred_at: dt.datetime
    #: The title was generated by the recorder or written by the minutes, never typed by a
    #: person — the screen marks it, so "schakl named this" and "I named this" differ.
    title_auto: bool = False
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
    #: While ``recording``, when the last piece landed (every piece bumps the row): a recorder
    #: posts one a minute, so a row untouched for longer is a tab that died, and the screen
    #: offers to process what was saved rather than waiting for a stop that will never come.
    updated_at: dt.datetime | None = None
    audio_file_id: uuid.UUID | None = None
    #: The recording's container, so a browser can say *before* pressing play whether it can
    #: play it (Safari on iOS plays no WebM) rather than drawing a dead control.
    audio_content_type: str | None = None
    segments: list[TranscriptSegment] = Field(default_factory=list)
    transcript_text: str | None = None
    #: Which transcription produced the words, and in how many parts — a split recording keeps
    #: one set of speaker labels per part, which the screen says beside the labels.
    transcript_model: str | None = None
    transcript_parts: int = 0
    #: A cut recording whose speakers were matched across the cuts (the parts overlapped and
    #: the provider answered timestamps): one label per person, not one per part.
    transcript_aligned: bool = False
    #: A cut recording whose later parts were handed a sample of every voice the earlier parts
    #: heard, so the speech model kept those labels by *voice* rather than the pipeline
    #: inferring them from timing (OpenAI's diarize model; ``transcript_aligned`` still says
    #: whether the overlap pairing ran for the rest).
    transcript_voiced: bool = False
    #: Whether the transcript labels speakers at all. ``False`` with a transcript means the
    #: speech model answered text only (``gpt-4o-transcribe``, ``gpt-transcribe``, whisper), and
    #: the screen says so by name rather than drawing an empty speaker list.
    diarized: bool = False
    #: Where the capture was lost and taken up again, in the recording's own clock. Empty for
    #: a recording nothing interrupted.
    recording_gaps: list[RecordingGap] = Field(default_factory=list)
    participants: list[MeetingParticipant] = Field(default_factory=list)
    #: Derived from ``participants``: label → name, for the transcript's lines.
    speakers: dict[str, str] = Field(default_factory=dict)
    minutes: MinutesDraft | None = None
    interaction_id: uuid.UUID | None = None
    task_ids: list[uuid.UUID] = Field(default_factory=list)
    #: The hours booked for the colleagues at the table, one row each — said on the page,
    #: because a time entry somebody did not type is a surprise on their timesheet unless the
    #: record says so.
    time_entries: list[MeetingTimeEntry] = Field(default_factory=list)
    #: The caller may edit the minutes, the roster and the filing: the keys the screen mirrors.
    can_write: bool = False
    can_delete: bool = False
    #: The caller may make a task of an action item here (the tasks module's own key), and
    #: may book the meeting's hours — for themself, and (``:any``) for the colleagues named.
    can_create_task: bool = False
    can_log_time_own: bool = False
    can_log_time_any: bool = False
    #: The sections a download of this meeting ticks by default (the org's settings, minus
    #: the ones this meeting has nothing for).
    document_sections: list[str] = Field(default_factory=list)


class MeetingStatusRead(BaseModel):
    """The one column the detail page polls while a worker holds the row."""

    status: MeetingStatus
    error_key: str | None = None
    status_at: dt.datetime


class MeetingTaskDraftRequest(BaseModel):
    """Ask schakl to draft the task for one action item of the stored minutes."""

    index: int = Field(ge=0, le=59)
    override_budget: bool = False


class MeetingTaskStep(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=2000)


class MeetingTaskLink(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(default=None, max_length=255)


class MeetingTaskCreate(BaseModel):
    """The reviewed draft of one action item, posted as the task it becomes.

    The tasks module's own create shape, minus the client: a meeting's task is always the
    meeting's client's (or its project's), and the service pins it. A contact of the client may
    hold it instead of a colleague — the "waiting on the client" shape (#273).
    """

    index: int = Field(ge=0, le=59)
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=20_000)
    due_date: dt.date
    priority: str | None = Field(default=None, max_length=10)
    status: str | None = Field(default=None, max_length=50)
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    assignee_contact_id: uuid.UUID | None = None
    label_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)
    allocated_minutes: int | None = Field(default=None, ge=0, le=100_000)
    checklist_title: str | None = Field(default=None, max_length=255)
    checklist_items: list[MeetingTaskStep] = Field(default_factory=list, max_length=100)
    links: list[MeetingTaskLink] = Field(default_factory=list, max_length=10)
    requires_interaction: bool = False
    visible_to_client: bool = False


class MeetingTaskCreated(BaseModel):
    """The task made from an action item, and the meeting as it now stands."""

    task_id: uuid.UUID
    meeting: MeetingDetail


# --- org settings ------------------------------------------------------------------------ #
class MeetingSettingsRead(BaseModel):
    """The org's meetings settings — the defaults where no row exists."""

    consent_required: bool = True
    document_design: str = "standard"
    document_accent_color: str | None = None
    document_cover_file_id: uuid.UUID | None = None
    document_footer_text: str | None = None
    document_sections: list[str] = Field(default_factory=lambda: list(DEFAULT_DOCUMENT_SECTIONS))
    document_avatars: bool = True
    document_custom_html: str | None = None
    document_custom_css: str | None = None
    ai_instructions: str | None = None
    #: Stated on the recorder and on the settings screen; the sweep reads the constant.
    audio_retention_days: int = 30


class MeetingSettingsUpdate(BaseModel):
    """A **partial** update: only the fields present in the body are written (§18's pair —
    absent means leave alone, an explicit ``null`` clears where the column is nullable)."""

    consent_required: bool | None = None
    document_design: str | None = Field(default=None, max_length=32)
    document_accent_color: str | None = Field(default=None, max_length=16)
    document_cover_file_id: uuid.UUID | None = None
    document_footer_text: str | None = Field(default=None, max_length=2000)
    document_sections: list[str] | None = Field(default=None, max_length=20)
    document_avatars: bool | None = None
    document_custom_html: str | None = None
    document_custom_css: str | None = None
    ai_instructions: str | None = Field(default=None, max_length=4000)

    @field_validator("document_sections")
    @classmethod
    def _known_sections(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unknown = [key for key in value if key not in DOCUMENT_SECTIONS]
        if unknown:
            raise ValueError("meetings.error.unknown_section")
        return list(dict.fromkeys(value))


class MeetingSettingsPreviewRequest(BaseModel):
    """An unsaved design, for the settings screen's live preview — the reporting editor's
    shape: the document fields only, so a preview never 422s on a field it does not draw."""

    document_design: str = Field(default="standard", max_length=32)
    document_accent_color: str | None = Field(default=None, max_length=16)
    document_cover_file_id: uuid.UUID | None = None
    document_footer_text: str | None = Field(default=None, max_length=2000)
    document_sections: list[str] | None = None
    document_avatars: bool = True
    document_custom_html: str | None = None
    document_custom_css: str | None = None


class MeetingDesignSource(BaseModel):
    """A shipped design's own HTML and CSS, to branch a custom one from."""

    html: str
    css: str


class MeetingPolicy(BaseModel):
    """What the recorder needs to know before it records: whether the consent statement is
    asked for, and how long the audio is kept. Readable by whoever may record."""

    consent_required: bool = True
    audio_retention_days: int = 30


class MeetingSectionCatalogEntry(BaseModel):
    key: str
    title_key: str
    #: Ticked by default on a download, per the org's settings.
    default: bool


# --- the transcript as a file ------------------------------------------------------------ #
class MeetingTranscript(BaseModel):
    """The words, whole: every segment with its speaker resolved to a name where the roster
    names one, and the flat text — what an agent reads and what a `.txt` export prints."""

    meeting_id: uuid.UUID
    title: str
    occurred_at: dt.datetime
    language: str | None = None
    model: str | None = None
    parts: int = 0
    diarized: bool = False
    speakers: dict[str, str] = Field(default_factory=dict)
    segments: list[TranscriptSegment] = Field(default_factory=list)
    text: str = ""


# --- changed in words ---------------------------------------------------------------------- #
class MeetingReviseRequest(BaseModel):
    """One typed instruction against one meeting — "zet de klant op Nova Fietsen, haal het
    tweede besluit weg en geef Sanne het eerste actiepunt". The words are the caller's own."""

    instruction: str = Field(min_length=1, max_length=4000)
    override_budget: bool = False


class MeetingReviseResult(BaseModel):
    """What the revision did, and the meeting as it now stands (the task revise's shape)."""

    meeting: MeetingDetail
    summary: str | None = None
    changed: list[str] = Field(default_factory=list)
    truncated: bool = False


class MeetingTranscribeRequest(TimeTranscribeRequest):
    """A recorded instruction for the revise box (#382's transport, one record over) — a
    distinct type on purpose, because the two services ask for different permissions."""
