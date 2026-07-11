"""Notification storage (CLAUDE.md §6, issue #16).

Five org-scoped tables, RLS-forced like every domain table:

* ``notification_events`` — one row per *thing that happened*, recipient-independent. It is
  the source for the per-record **activity feed** as well as the fan-out. ``entity_type`` /
  ``entity_id`` are polymorphic (a task, a project, a company, a leave request, a timesheet)
  so there is deliberately **no** FK on ``entity_id`` — the row outlives the entity and the
  UI renders from the ``payload`` snapshot.
* ``notifications`` — one row per *recipient* of an event: this table **is** the in-app
  channel. ``visible_at`` implements digests without a synthetic row (a daily-digest event is
  written now but only surfaces at 08:00); ``read_at`` is the reversible read toggle.
* ``notification_watchers`` — explicit watch / mute of a record, so someone who is not an
  assignee can still follow it (or an assignee can silence it).
* ``notification_preferences`` — the per-user × event × channel matrix, with ``user_id NULL``
  rows as the org default (the ``dashboard_prefs`` pattern) and ``event_type NULL`` rows as
  the per-scope "general" block (quiet hours + due-soon threshold).
* ``notification_deliveries`` — the seam issue #17 (external transports) writes to; the
  in-app channel is pull, so it never writes a delivery row.

All user-facing text is an i18n key rendered in the *recipient's* locale — ``event_type`` maps
to ``notifications.event.*`` and the payload carries only i18n params + snapshotted titles.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class NotificationEvent(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One recorded happening; recipient-independent. Also serves the activity feed."""

    __tablename__ = "notification_events"
    __table_args__ = (
        Index(
            "ix_notification_events_entity",
            "org_id",
            "entity_type",
            "entity_id",
            "created_at",
        ),
        # A cron re-emits candidates every day; the dedup key stops the second emit from
        # writing a duplicate event. Partial: only keyed events participate.
        Index(
            "uq_notification_events_dedup",
            "org_id",
            "dedup_key",
            unique=True,
            postgresql_where=text("dedup_key IS NOT NULL"),
        ),
    )

    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    # Polymorphic, cross-module → deliberately NO FK; the row survives the entity's deletion.
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    # NULL actor = the system (a cron reminder, an automation).
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # i18n params + an ``entity_title`` snapshot so the feed reads correctly after a rename.
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
    dedup_key: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Notification(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One recipient's copy of an event — this table is the in-app channel."""

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_user", "org_id", "user_id", "created_at"),
        # The bell's unread count is exactly this partial index (cheap, hot path).
        Index(
            "ix_notifications_unread",
            "org_id",
            "user_id",
            "visible_at",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("notification_events.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Digest/delay: the row exists now but the bell only counts it once now() reaches this.
    visible_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class NotificationWatcher(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Explicit follow (``muted=False``) or suppress (``muted=True``) of one record.

    No row = the default fan-out (assignees + hints). A ``muted=True`` row drops the user
    even when an event hints them; a ``muted=False`` row adds a non-assignee as a recipient.
    """

    __tablename__ = "notification_watchers"
    __table_args__ = (
        UniqueConstraint(
            "org_id", "user_id", "entity_type", "entity_id",
            name="uq_notification_watchers_org_id",
        ),
        Index("ix_notification_watchers_entity", "org_id", "entity_type", "entity_id"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    muted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class NotificationPreference(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The per-user × event × channel matrix.

    ``user_id IS NULL`` → the org default row (managers curate it, dashboard_prefs pattern).
    ``event_type IS NULL`` → the "general" row for a scope: quiet hours + due-soon threshold,
    which are not per-event. Four partial unique indexes keep each quadrant single-valued.
    """

    __tablename__ = "notification_preferences"
    __table_args__ = (
        # user + specific event
        Index(
            "uq_notif_pref_user_event",
            "org_id", "user_id", "event_type", "channel",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND event_type IS NOT NULL"),
        ),
        # user + general (event_type NULL)
        Index(
            "uq_notif_pref_user_general",
            "org_id", "user_id", "channel",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL AND event_type IS NULL"),
        ),
        # org default + specific event
        Index(
            "uq_notif_pref_org_event",
            "org_id", "event_type", "channel",
            unique=True,
            postgresql_where=text("user_id IS NULL AND event_type IS NOT NULL"),
        ),
        # org default + general
        Index(
            "uq_notif_pref_org_general",
            "org_id", "channel",
            unique=True,
            postgresql_where=text("user_id IS NULL AND event_type IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    event_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    channel: Mapped[str] = mapped_column(
        String(20), nullable=False, default="in_app", server_default="in_app"
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    delay_minutes: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # immediate | hourly | daily | weekly
    digest: Mapped[str] = mapped_column(
        String(10), nullable=False, default="immediate", server_default="immediate"
    )
    digest_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    # 0 = Monday … 6 = Sunday (for the weekly digest).
    digest_weekday: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quiet_hours_start: Mapped[time | None] = mapped_column(Time, nullable=True)
    quiet_hours_end: Mapped[time | None] = mapped_column(Time, nullable=True)
    # General-row only: how many days ahead counts as "due soon".
    due_soon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class NotificationDelivery(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """External-transport delivery attempts (issue #17 seam).

    The in-app channel is pull (the ``notifications`` table is the inbox), so it writes no
    rows here. SMTP / Slack / Teams (#17) enqueue one row per attempt and update its status.
    """

    __tablename__ = "notification_deliveries"
    __table_args__ = (
        Index(
            "ix_notification_deliveries_pending",
            "org_id",
            "created_at",
            postgresql_where=text("status = 'pending'"),
        ),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Which configured external channel this attempt targets (#17). ``NULL`` for the in-app
    #: channel, which writes no rows here anyway.
    channel_config_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("notification_channels.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NotificationChannelConfig(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A configured external transport (#17): an Apprise URL, encrypted at rest.

    ``kind`` is the transport family (``slack``, ``msteams``, ``gchat``, ``mailto``, ``webhook``)
    for display and SSRF policy; ``url_enc`` is the full Apprise URL — it embeds bot tokens and
    webhook secrets, so it is **encrypted** (:mod:`app.core.crypto`) and never returned by the
    API, only a redacted preview. ``event_filter`` is the event types this channel receives
    (empty = all). ``user_id`` distinguishes a personal channel (my Slack DM) from an org channel
    (the team's ``#crm`` room); ``NULL`` = org-wide.
    """

    __tablename__ = "notification_channels"

    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    url_enc: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: Event types routed to this channel; ``[]`` means every event.
    event_filter: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
