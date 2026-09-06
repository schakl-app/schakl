"""microsoft.outlook — the suppression list and the two persisted skips (docs/MICROSOFT.md §6).

A rejected email must never come back: its Graph message id (and, when the owner chose so, its
whole conversation) lands here, and the poller skips suppressed ids before anything else looks at
the message. Per-connection: suppression is the mailbox owner's decision about *their* mailbox,
not a tenant-wide blocklist. ``outlook_skips`` holds exactly the two ingest failures a person
would never know to go looking for (``app/core/mailbox/gates.py``) — ids, a reason, a timestamp,
never content — reaped after a retention window.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class OutlookSuppression(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "outlook_suppressions"
    __table_args__ = (
        Index(
            "uq_outlook_suppressions_org_conn_message",
            "org_id",
            "connection_id",
            "message_id",
            unique=True,
            postgresql_where=text("message_id IS NOT NULL"),
        ),
        Index(
            "ix_outlook_suppressions_org_conn_conversation",
            "org_id",
            "connection_id",
            "conversation_id",
        ),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("microsoft_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: At least one of the two is set: a message-level or a conversation-level suppression.
    message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    conversation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)


class OutlookSkip(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The two ingest skips that are failures rather than policy — see ``GmailSkip`` for the
    reasoning, which is the same one feed over: ids, a reason and a timestamp, no content."""

    __tablename__ = "outlook_skips"
    __table_args__ = (
        Index(
            "uq_outlook_skips_org_conn_message",
            "org_id",
            "connection_id",
            "message_id",
            unique=True,
        ),
        Index("ix_outlook_skips_org_created", "org_id", "created_at"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("microsoft_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    message_id: Mapped[str] = mapped_column(String(512), nullable=False)
    conversation_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: A :class:`~app.core.mailbox.gates.SkipReason` value, stored as text.
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
