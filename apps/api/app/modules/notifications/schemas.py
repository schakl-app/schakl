"""Request/response models for ``/api/v1/notifications`` (CLAUDE.md §9, issue #16).

``payload`` is deliberately an open dict: it holds the i18n parameters for
``notifications.event.<event_type>`` (plus an ``entity_title`` snapshot), and the client
renders the sentence in the *reader's* locale. The API never ships a translated string.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

from app.modules.notifications.events import DIGEST_CADENCES, ENTITY_TYPES, EVENT_TYPES

EntityType = Literal["task", "project", "company", "leave_request", "timesheet"]
PrefSource = Literal["default", "org", "user"]


class NotificationRead(BaseModel):
    id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: uuid.UUID
    # None ⇒ the system acted (a cron reminder), not a person.
    actor_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    read_at: datetime | None = None
    visible_at: datetime
    created_at: datetime


class ActivityItem(BaseModel):
    """One line of a record's activity feed — recipient-independent."""

    id: uuid.UUID
    event_type: str
    entity_type: str
    entity_id: uuid.UUID
    actor_name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class UnreadCount(BaseModel):
    count: int


class ReadUpdate(BaseModel):
    read: bool


class MarkAllResult(BaseModel):
    updated: int


class WatchRead(BaseModel):
    """Tri-state: ``True`` following, ``False`` muted, ``None`` the default fan-out."""

    watching: bool | None = None


class WatchUpdate(BaseModel):
    entity_type: EntityType
    entity_id: uuid.UUID
    watching: bool | None = None


class PreferenceRow(BaseModel):
    """One event's effective delivery rules (in-app + e-mail), and which layer decided each."""

    event_type: str
    enabled: bool
    delay_minutes: int
    digest: str
    digest_time: time | None = None
    digest_weekday: int | None = None
    source: PrefSource
    # E-mail channel (#245): the same granularity as in-app, resolved independently. ``off`` is
    # ``email_enabled=false``; the digest schedule (time/weekday) lives on the matrix's ``email``.
    email_enabled: bool = False
    email_delay_minutes: int = 0
    email_digest: str = "immediate"
    email_source: PrefSource = "default"


class GeneralPreference(BaseModel):
    due_soon_days: int
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    source: PrefSource


class EmailSchedule(BaseModel):
    """The scope's global e-mail digest schedule: when its daily/weekly mails leave (#245)."""

    digest_time: time | None = None
    digest_weekday: int | None = None
    source: PrefSource


class ChannelPreferenceEvent(BaseModel):
    """One event's rule on one external channel (#283). ``enabled=false`` = not routed."""

    event_type: str
    enabled: bool = False
    delay_minutes: int = 0
    digest: str = "immediate"


class ChannelPreference(BaseModel):
    """An external channel as the matrix renders it: one column, one row per event.

    ``digest_time``/``digest_weekday`` are the channel's own digest schedule — not per event, so
    they are edited on the channel (Instellingen → Meldingen → Kanalen), not in the matrix.
    """

    id: uuid.UUID
    name: str
    kind: str
    digest_time: time | None = None
    digest_weekday: int | None = None
    events: list[ChannelPreferenceEvent] = Field(default_factory=list)


class PreferenceMatrix(BaseModel):
    events: list[PreferenceRow]
    general: GeneralPreference
    email: EmailSchedule
    #: This scope's external channels, each with its per-event rules (#283, #295): the caller's
    #: own transports on the personal matrix, the org's shared rooms on the default one.
    channels: list[ChannelPreference] = Field(default_factory=list)


class PreferenceRowWrite(BaseModel):
    event_type: str
    enabled: bool = True
    delay_minutes: Annotated[int, Field(ge=0, le=24 * 60)] = 0
    digest: str = "immediate"
    digest_time: time | None = None
    digest_weekday: Annotated[int | None, Field(ge=0, le=6)] = None

    @field_validator("event_type")
    @classmethod
    def _known_event(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError("unknown event_type")
        return value

    @field_validator("digest")
    @classmethod
    def _known_digest(cls, value: str) -> str:
        if value not in DIGEST_CADENCES:
            raise ValueError("unknown digest cadence")
        return value


class EmailPreferenceRowWrite(BaseModel):
    """One event's e-mail override (#245). The digest schedule is global, so no time/weekday."""

    event_type: str
    enabled: bool = False
    delay_minutes: Annotated[int, Field(ge=0, le=24 * 60)] = 0
    digest: str = "immediate"

    @field_validator("event_type")
    @classmethod
    def _known_event(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError("unknown event_type")
        return value

    @field_validator("digest")
    @classmethod
    def _known_digest(cls, value: str) -> str:
        if value not in DIGEST_CADENCES:
            raise ValueError("unknown digest cadence")
        return value


class GeneralPreferenceWrite(BaseModel):
    due_soon_days: Annotated[int | None, Field(ge=0, le=90)] = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None


class EmailScheduleWrite(BaseModel):
    digest_time: time | None = None
    digest_weekday: Annotated[int | None, Field(ge=0, le=6)] = None


class ChannelPreferenceEventWrite(BaseModel):
    """One event routed to one channel. Absent = not routed (#283)."""

    event_type: str
    enabled: bool = False
    delay_minutes: Annotated[int, Field(ge=0, le=24 * 60)] = 0
    digest: str = "immediate"

    @field_validator("event_type")
    @classmethod
    def _known_event(cls, value: str) -> str:
        if value not in EVENT_TYPES:
            raise ValueError("unknown event_type")
        return value

    @field_validator("digest")
    @classmethod
    def _known_digest(cls, value: str) -> str:
        if value not in DIGEST_CADENCES:
            raise ValueError("unknown digest cadence")
        return value


class ChannelPreferenceWrite(BaseModel):
    """This channel's whole per-event routing, wholesale like every other block."""

    channel_config_id: uuid.UUID
    events: list[ChannelPreferenceEventWrite] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _no_duplicates(
        cls, value: list[ChannelPreferenceEventWrite]
    ) -> list[ChannelPreferenceEventWrite]:
        if len({row.event_type for row in value}) != len(value):
            raise ValueError("duplicate event_type")
        return value


class PreferenceUpdate(BaseModel):
    """A PUT replaces this scope's overrides **wholesale** — an omitted event inherits again.

    The body is a full snapshot of the scope, not a patch: ``events`` and ``email_events`` are
    the in-app and e-mail overrides (each channel tracked independently, so an event may override
    one channel while inheriting the other), and ``general`` / ``email`` are the two scope-wide
    rows. Whatever a channel's list does not contain is cleared, exactly as omitting an ``events``
    entry clears that in-app override. A caller that means to change one channel must therefore
    still send the other channel's current overrides, or they are dropped — the web form always
    posts both. This mirrors the pre-#245 behaviour of ``events``/``general``; e-mail simply joined
    the same wholesale scope when its dedicated endpoint was folded in.
    """

    events: list[PreferenceRowWrite] = Field(default_factory=list)
    email_events: list[EmailPreferenceRowWrite] = Field(default_factory=list)
    general: GeneralPreferenceWrite | None = None
    email: EmailScheduleWrite | None = None
    #: Per external channel (#283, #295), wholesale like every other block: what this list does
    #: not carry is cleared, so a caller that means to change one channel still sends the others
    #: (the web form always posts every column). Each id must belong to the scope being written —
    #: the caller's own channels here, the org's shared rooms on the org-default endpoint.
    channels: list[ChannelPreferenceWrite] = Field(default_factory=list)

    @field_validator("events")
    @classmethod
    def _no_duplicates(cls, value: list[PreferenceRowWrite]) -> list[PreferenceRowWrite]:
        seen = {row.event_type for row in value}
        if len(seen) != len(value):
            raise ValueError("duplicate event_type")
        return value

    @field_validator("email_events")
    @classmethod
    def _no_email_duplicates(
        cls, value: list[EmailPreferenceRowWrite]
    ) -> list[EmailPreferenceRowWrite]:
        seen = {row.event_type for row in value}
        if len(seen) != len(value):
            raise ValueError("duplicate event_type")
        return value


__all__ = [
    "ENTITY_TYPES",
    "ActivityItem",
    "ChannelPreference",
    "ChannelPreferenceEvent",
    "ChannelPreferenceEventWrite",
    "ChannelPreferenceWrite",
    "EmailPreferenceRowWrite",
    "EmailSchedule",
    "EmailScheduleWrite",
    "EntityType",
    "GeneralPreference",
    "GeneralPreferenceWrite",
    "MarkAllResult",
    "NotificationRead",
    "PreferenceMatrix",
    "PreferenceRow",
    "PreferenceRowWrite",
    "PreferenceUpdate",
    "ReadUpdate",
    "UnreadCount",
    "WatchRead",
    "WatchUpdate",
]


# --- external channels (#17) --------------------------------------------------- #
# ``email`` sends to a recipient address through the org's own transport (Instellingen →
# E-mail, ``app.core.email``); the rest are Apprise families.
CHANNEL_KINDS = Literal[
    "email", "slack", "msteams", "gchat", "discord", "telegram", "mailto", "webhook", "custom"
]


class ChannelCreate(BaseModel):
    """Connect a transport. **Which events reach it, and how often, is not asked here** (#295):
    that is a per-event column in the matrix of the scope that owns the channel, so a freshly
    connected channel is silent until someone routes something to it.
    """

    kind: CHANNEL_KINDS
    name: str = Field(min_length=1, max_length=120)
    #: The full Apprise URL. Write-only: encrypted at rest, never returned (#17).
    url: str = Field(min_length=1)
    enabled: bool = True
    #: A personal channel (my DM) when set to a member; ``None`` = an org channel.
    user_id: uuid.UUID | None = None
    #: This channel's digest *schedule*: which hour, which weekday its bundles land on. One
    #: question per channel rather than one per matrix row, because it has one answer.
    digest_time: time | None = None
    digest_weekday: Annotated[int | None, Field(ge=0, le=6)] = None


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    #: Rotate the URL by sending a new one; omit to leave it unchanged.
    url: str | None = None
    enabled: bool | None = None
    digest_time: time | None = None
    digest_weekday: Annotated[int | None, Field(ge=0, le=6)] = None


class ChannelRead(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    #: A redacted preview (``slack://xoxb-****``) — never the secret-bearing URL.
    redacted: str
    enabled: bool
    #: ``None`` = a shared room the org routes; set = that person's own transport.
    user_id: uuid.UUID | None
    digest_time: time | None = None
    digest_weekday: int | None = None
    created_at: datetime


class ChannelTestResult(BaseModel):
    ok: bool
    #: The provider's own error, surfaced verbatim so a broken webhook is diagnosable (#17).
    error: str | None = None
