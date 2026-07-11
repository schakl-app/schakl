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
    """One event's effective delivery rule, and which layer decided it."""

    event_type: str
    enabled: bool
    delay_minutes: int
    digest: str
    digest_time: time | None = None
    digest_weekday: int | None = None
    source: PrefSource


class GeneralPreference(BaseModel):
    due_soon_days: int
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    source: PrefSource


class PreferenceMatrix(BaseModel):
    events: list[PreferenceRow]
    general: GeneralPreference


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


class GeneralPreferenceWrite(BaseModel):
    due_soon_days: Annotated[int | None, Field(ge=0, le=90)] = None
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None


class PreferenceUpdate(BaseModel):
    """A PUT replaces this scope's overrides wholesale — an omitted event inherits again."""

    events: list[PreferenceRowWrite] = Field(default_factory=list)
    general: GeneralPreferenceWrite | None = None

    @field_validator("events")
    @classmethod
    def _no_duplicates(cls, value: list[PreferenceRowWrite]) -> list[PreferenceRowWrite]:
        seen = {row.event_type for row in value}
        if len(seen) != len(value):
            raise ValueError("duplicate event_type")
        return value


__all__ = [
    "ENTITY_TYPES",
    "ActivityItem",
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
CHANNEL_KINDS = Literal[
    "slack", "msteams", "gchat", "discord", "telegram", "mailto", "webhook"
]


class ChannelCreate(BaseModel):
    kind: CHANNEL_KINDS
    name: str = Field(min_length=1, max_length=120)
    #: The full Apprise URL. Write-only: encrypted at rest, never returned (#17).
    url: str = Field(min_length=1)
    enabled: bool = True
    #: Event types routed here; empty = all. Validated against the known set.
    event_filter: list[str] = Field(default_factory=list)
    #: A personal channel (my DM) when set to a member; ``None`` = an org channel.
    user_id: uuid.UUID | None = None

    @field_validator("event_filter")
    @classmethod
    def _known_events(cls, value: list[str]) -> list[str]:
        for event in value:
            if event not in EVENT_TYPES:
                raise ValueError("errors.validation")
        return value


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    #: Rotate the URL by sending a new one; omit to leave it unchanged.
    url: str | None = None
    enabled: bool | None = None
    event_filter: list[str] | None = None

    @field_validator("event_filter")
    @classmethod
    def _known_events(cls, value: list[str] | None) -> list[str] | None:
        if value is not None:
            for event in value:
                if event not in EVENT_TYPES:
                    raise ValueError("errors.validation")
        return value


class ChannelRead(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    kind: str
    name: str
    #: A redacted preview (``slack://xoxb-****``) — never the secret-bearing URL.
    redacted: str
    enabled: bool
    event_filter: list[str]
    user_id: uuid.UUID | None
    created_at: datetime


class ChannelTestResult(BaseModel):
    ok: bool
    #: The provider's own error, surfaced verbatim so a broken webhook is diagnosable (#17).
    error: str | None = None
