"""google.calendar — watch/sync state, the local event cache, and the leave-push outbox.

Three tables, three jobs (docs/GOOGLE.md §4):

- ``google_calendar_channels`` — one row per connection: the watch channel Google pushes to
  and the ``syncToken`` incremental-sync cursor. A channel that cannot register (no public
  HTTPS — dev boxes) parks on ``watch_status=failed`` and the poll-fallback cron carries it.
- ``google_calendar_events`` — the minimal local cache the Agenda reads. **Never** queried
  live from Google on a page load (docs/PERFORMANCE.md); the sync worker maintains it.
- ``calendar_event_links`` — the push outbox: local record → Google event. Event-bus handlers
  only ever write a row here (they run in the emitter's transaction — no external calls);
  the worker does the Google I/O. ``payload`` snapshots the event body at emit time so the
  worker never re-reads another module's internals.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class WatchStatus(StrEnum):
    NONE = "none"
    ACTIVE = "active"
    FAILED = "failed"  # registration refused (typically: no public HTTPS) → polling carries it


class LinkStatus(StrEnum):
    PENDING = "pending"
    PUSHED = "pushed"
    DELETE_PENDING = "delete_pending"
    FAILED = "failed"


class GoogleCalendarChannel(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "google_calendar_channels"
    __table_args__ = (
        UniqueConstraint("org_id", "connection_id", name="uq_gcal_channels_org_connection"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    calendar_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default="primary", server_default="primary"
    )
    #: We mint ``channel_id`` (uuid4) and ``channel_token`` (the webhook's shared secret);
    #: ``resource_id`` is Google's handle, needed to stop the channel.
    channel_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    resource_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    channel_token: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watch_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=WatchStatus.NONE.value, server_default="none"
    )
    sync_token: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class GoogleCalendarEvent(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One cached Google event, deliberately minimal: what the Agenda chip needs, nothing more
    (no description, no attendees — the deep link opens Google for the rest)."""

    __tablename__ = "google_calendar_events"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "connection_id", "google_event_id", name="uq_gcal_events_org_conn_event"
        ),
        Index("ix_gcal_events_org_conn_start_at", "org_id", "connection_id", "start_at"),
        Index("ix_gcal_events_org_conn_start_date", "org_id", "connection_id", "start_date"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    google_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    calendar_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default="primary", server_default="primary"
    )
    summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="confirmed", server_default="confirmed"
    )
    html_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Timed events use the instant pair; all-day events the date pair (Google's own split).
    all_day: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: Google's all-day ``end.date`` is exclusive; stored as-is, made inclusive at read time.
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at_google: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class CalendarEventLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The push outbox: one local record ↔ one Google event (docs/GOOGLE.md §4)."""

    __tablename__ = "calendar_event_links"
    __table_args__ = (
        UniqueConstraint("org_id", "local_type", "local_id", name="uq_gcal_links_org_local"),
        Index("ix_gcal_links_org_status", "org_id", "status"),
    )

    local_type: Mapped[str] = mapped_column(String(32), nullable=False)
    #: No FK — the link must outlive the record so a deleted leave request still deletes its
    #: pushed Google event instead of stranding it.
    local_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="SET NULL"),
        nullable=True,
    )
    calendar_id: Mapped[str] = mapped_column(
        String(255), nullable=False, default="primary", server_default="primary"
    )
    google_event_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    etag: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=LinkStatus.PENDING.value, server_default="pending"
    )
    #: The Google event body, snapshotted in the emitter's transaction — the worker builds the
    #: API call from this and never re-reads leave internals.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
