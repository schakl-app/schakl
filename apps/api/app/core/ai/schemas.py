"""Pydantic shapes for the AI core (#126) and its features (#127–#130)."""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.core.ai.audio import MAX_ENCODED_CHARS
from app.core.ai.models import AI_FEATURES, AI_PROVIDERS

Provider = Literal["anthropic", "openai", "openai_compatible"]
#: Transcription speaks one API shape (`POST {base}/audio/transcriptions`), and Anthropic has
#: no speech endpoint at all — so the speech provider is a strictly narrower set than the chat
#: one, rather than the same Literal reused.
SpeechProvider = Literal["openai", "openai_compatible"]

_PERIOD = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class AIFeatureConfig(BaseModel):
    enabled: bool = True
    #: Optional per-feature model override; None = the org's default model.
    model: str | None = Field(default=None, max_length=255)


class AISettingsWrite(BaseModel):
    provider: Provider
    #: Write-only. Empty on an update means "keep the stored key".
    api_key: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=1024)
    default_model: str | None = Field(default=None, max_length=255)
    features: dict[str, AIFeatureConfig] = Field(default_factory=dict)
    house_style: str | None = Field(default=None, max_length=4000)
    monthly_token_budget: int | None = Field(default=None, ge=1)
    # --- speech-to-text (#246) --------------------------------------------------- #
    #: NULL/empty means "reuse the chat provider", which only resolves for one that can
    #: transcribe — Anthropic cannot, so an Anthropic org sets these to dictate.
    speech_provider: SpeechProvider | None = None
    #: Write-only, same rule as ``api_key``.
    speech_api_key: str | None = Field(default=None, max_length=2000)
    speech_base_url: str | None = Field(default=None, max_length=1024)
    speech_model: str | None = Field(default=None, max_length=255)
    monthly_audio_seconds_budget: int | None = Field(default=None, ge=1)


class AISettingsRead(BaseModel):
    provider: Provider
    base_url: str | None
    default_model: str
    has_key: bool
    features: dict[str, AIFeatureConfig]
    house_style: str | None
    monthly_token_budget: int | None
    speech_provider: SpeechProvider | None = None
    speech_base_url: str | None = None
    speech_model: str | None = None
    has_speech_key: bool = False
    monthly_audio_seconds_budget: int | None = None
    #: Whether a microphone should be offered at all — a resolved answer, so the UI never has
    #: to re-derive "can this provider transcribe" from the provider name.
    speech_available: bool = False


class AITestResult(BaseModel):
    """Round-trip result of the settings page's test button; ``error`` is the provider's
    failure verbatim — the one place raw provider text reaches the UI on purpose."""

    ok: bool
    model: str | None = None
    error: str | None = None


class AIModelsRequest(BaseModel):
    """Inputs for the live model listing. Everything optional: empty values fall back to
    the stored settings, so the picker works both during first setup (key just typed,
    nothing saved) and afterwards (key stored, never played back)."""

    provider: Provider | None = None
    api_key: str | None = Field(default=None, max_length=2000)
    base_url: str | None = Field(default=None, max_length=1024)


class AIModelsResult(BaseModel):
    """Settings-page helper semantics like the test button: a provider failure is data
    (verbatim in ``error``), never a 500."""

    models: list[str] = Field(default_factory=list)
    error: str | None = None


class AIUsageFeature(BaseModel):
    feature: str
    tokens_in: int
    tokens_out: int
    requests: int


class AIUsageSummary(BaseModel):
    """This calendar month's metering, for the settings-page meter (#126)."""

    month: str
    tokens_total: int
    budget: int | None
    features: list[AIUsageFeature]


# --------------------------------------------------------------------------- #
# Features
# --------------------------------------------------------------------------- #
WritingAction = Literal[
    "improve", "shorten", "expand", "fix", "tone_business", "tone_informal",
    "translate", "draft",
]


class WritingAssistRequest(BaseModel):
    action: WritingAction
    text: str = Field(min_length=1, max_length=40_000)
    #: Naming context only — never the record graph (#128 scope discipline).
    entity_type: str | None = Field(default=None, max_length=40)
    title: str | None = Field(default=None, max_length=255)
    #: For ``translate``: the language to translate into (nl or en).
    target_locale: str | None = Field(default=None, max_length=8)
    override_budget: bool = False


class AssistantContext(BaseModel):
    entity_type: str = Field(max_length=40)
    entity_id: uuid.UUID
    label: str | None = Field(default=None, max_length=255)


class AssistantMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=40_000)


class AssistantRequest(BaseModel):
    messages: list[AssistantMessage] = Field(min_length=1, max_length=40)
    context: AssistantContext | None = None
    override_budget: bool = False


class TimeTranscribeRequest(BaseModel):
    """A recorded quick-add line (#246).

    The clip rides base64-in-JSON rather than multipart: the web app reaches the API through
    one same-origin proxy that forwards JSON, and a second transport for one endpoint would be
    a worse trade than 33% on a clip measured in tens of kilobytes.
    """

    #: Above the decoder's cap, deliberately: the decoder answers a too-long clip with a 413
    #: that names the cause, and a schema bound at or below it would answer first with a 422
    #: naming nothing. Derived from the cap rather than typed (it was a literal 12 MB against an
    #: 8 MB cap, and became the tighter of the two the day the cap grew).
    audio: str = Field(min_length=1, max_length=MAX_ENCODED_CHARS * 2)
    #: BCP-47-ish hint for the recogniser; the caller's own locale, never a hardcoded nl.
    language: str | None = Field(default=None, max_length=8)
    override_budget: bool = False


class TimeTranscribeResult(BaseModel):
    """Just the words. The transcript goes back into the quick-add field for the user to read
    and fix before it is parsed — a misheard client name is the failure mode worth catching,
    and it is only catchable while the text is still visible."""

    text: str


class TaskTranscribeRequest(TimeTranscribeRequest):
    """A recorded task dictation (#382).

    Identical on the wire to :class:`TimeTranscribeRequest` — the same clip, the same base64,
    the same caps — and a distinct type on purpose: the two services ask for different
    permissions, and one shared request model is what makes the next reader assume they are
    one call.
    """


class AssistantTranscribeRequest(TimeTranscribeRequest):
    """A spoken question or instruction for the assistant.

    Same wire shape as the other two, and the third distinct type on purpose: this one asks
    for the ``assistant`` feature and no write permission at all, because what the transcript
    becomes is a chat message the user still has to send.
    """


class TaskDraftChecklistItem(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = Field(default=None, max_length=2000)


class TaskDraftLink(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(default=None, max_length=255)


class TaskParseRequest(BaseModel):
    #: Longer than the time parse's 2000: a spoken task carries its steps, and 2000 characters
    #: is about ninety seconds of dictation.
    text: str = Field(min_length=1, max_length=8000)
    #: The org's today unless the client says otherwise — the #129 rule: "volgende vrijdag"
    #: resolved against the server's UTC day is a day out for several hours every night.
    today: dt.date | None = None
    #: Pin the draft to the client / project the surface already knows (a task dictated from a
    #: company page). A **default the speaker overrides**, never a filter: naming another
    #: client still wins, because a draft that silently disagrees with the words it was made
    #: from is worse than one that needs a correction.
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    override_budget: bool = False


class TaskParseResult(BaseModel):
    """A *draft* task: prefills the review form and creates nothing (#129's rule, #382's feature).

    Every field is optional and an unstated one stays ``None`` — the two booleans included,
    whose third state is what lets the form keep the platform's own defaults instead of
    recording a decision nobody made. ``billable``'s lesson (#284), one module over.

    The vocabulary is wide **because the input is a colleague's own voice and a human presses
    the button**. #327's narrow ``TaskEnrichment`` exists because an email is written by an
    outsider and applied by a worker with nobody watching; copying its omissions here would
    keep the shape and drop the reason, and the only effect would be the speaker retyping the
    half the schema refused to carry.
    """

    title: str | None = None
    description: str | None = None
    due_date: dt.date | None = None
    #: One of ``TaskPriority``; ``None`` when the words implied nothing.
    priority: str | None = None
    #: A key from the org's own ``task_statuses``, grounded by membership in that set.
    status: str | None = None
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    label_ids: list[uuid.UUID] = Field(default_factory=list)
    allocated_minutes: int | None = None
    checklist_title: str | None = None
    checklist_items: list[TaskDraftChecklistItem] = Field(default_factory=list)
    links: list[TaskDraftLink] = Field(default_factory=list)
    requires_interaction: bool | None = None
    visible_to_client: bool | None = None
    #: True when the model's answer ran out of room. The review still opens — a partial draft
    #: beats a lost dictation — but the surface says so rather than presenting a cut-off plan
    #: as a complete one (docs/AI.md, "a truncated answer is not an empty one").
    truncated: bool = False


class TimeParseRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    #: The day the user is looking at. "vanmiddag 2 uur" typed while viewing last Tuesday means
    #: *that* Tuesday; without this the server answers with its own today and the client then
    #: navigates the user off the day they were working on. Absent = the org's today.
    today: dt.date | None = None
    override_budget: bool = False


class TimeParseResult(BaseModel):
    """A *draft* entry: prefills the form, never creates anything (#129).

    Every field is optional and an unstated one stays ``None`` — notably ``billable``, whose
    third state is what lets the form keep the project's own default (#284, #246). A ``False``
    here would be indistinguishable from the user having said "niet declarabel".
    """

    date: dt.date | None = None
    start: str | None = None
    end: str | None = None
    duration_minutes: int | None = None
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    description: str | None = None
    #: A key from the org's own ``time_entry_types`` (#176), or None.
    entry_type_key: str | None = None
    billable: bool | None = None
    break_minutes: int | None = None


class TimeReconstructRequest(BaseModel):
    date: dt.date
    override_budget: bool = False


class TimeSuggestion(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    minutes: int | None = None
    description: str = ""
    label: str = ""


class TimeReconstructResult(BaseModel):
    short: bool
    scheduled_minutes: int
    logged_minutes: int
    leave_minutes: int
    suggestions: list[TimeSuggestion] = Field(default_factory=list)


class DigestRequest(BaseModel):
    override_budget: bool = False


class ReportGenerateRequest(BaseModel):
    company_id: uuid.UUID
    period: str = Field(pattern=_PERIOD.pattern)
    language: str = Field(default="nl", max_length=8)
    override_budget: bool = False


class ReportCreate(BaseModel):
    company_id: uuid.UUID
    period: str = Field(pattern=_PERIOD.pattern)
    language: str = Field(default="nl", max_length=8)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(default="", max_length=200_000)


class ReportUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = Field(default=None, max_length=200_000)


class ReportRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    period: str
    language: str
    title: str
    content: str
    created_by_name: str | None
    created_at: dt.datetime
    updated_at: dt.datetime

    model_config = {"from_attributes": True}


__all__ = [
    "AI_FEATURES",
    "AI_PROVIDERS",
    "AIFeatureConfig",
    "AISettingsRead",
    "AISettingsWrite",
    "AITestResult",
    "AIModelsRequest",
    "AIModelsResult",
    "AIUsageFeature",
    "AIUsageSummary",
    "AssistantContext",
    "AssistantMessage",
    "AssistantRequest",
    "DigestRequest",
    "ReportCreate",
    "ReportGenerateRequest",
    "ReportRead",
    "ReportUpdate",
    "TaskDraftChecklistItem",
    "TaskDraftLink",
    "TaskParseRequest",
    "TaskParseResult",
    "TaskTranscribeRequest",
    "TimeParseRequest",
    "TimeParseResult",
    "TimeReconstructRequest",
    "TimeReconstructResult",
    "TimeSuggestion",
    "TimeTranscribeRequest",
    "TimeTranscribeResult",
    "WritingAssistRequest",
]
