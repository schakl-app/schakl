"""google.gmail — the suppression list (docs/GOOGLE.md §6, the owner's opt-out).

A rejected email must never come back: its Gmail message id (and, when the owner chose so,
its whole thread) lands here, and the poller skips suppressed ids before anything else looks
at the message. Per-connection: suppression is the mailbox owner's decision about *their*
mailbox, not a tenant-wide blocklist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class GmailSuppression(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "gmail_suppressions"
    __table_args__ = (
        Index(
            "uq_gmail_suppressions_org_conn_message",
            "org_id",
            "connection_id",
            "gmail_message_id",
            unique=True,
            postgresql_where=text("gmail_message_id IS NOT NULL"),
        ),
        Index(
            "ix_gmail_suppressions_org_conn_thread",
            "org_id",
            "connection_id",
            "gmail_thread_id",
        ),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: At least one of the two is set: a message-level or a thread-level suppression.
    gmail_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
