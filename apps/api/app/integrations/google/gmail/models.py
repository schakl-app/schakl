"""google.gmail — the suppression list (docs/GOOGLE.md §6, the owner's opt-out).

A rejected email must never come back: its Gmail message id (and, when the owner chose so,
its whole thread) lands here, and the poller skips suppressed ids before anything else looks
at the message. Per-connection: suppression is the mailbox owner's decision about *their*
mailbox, not a tenant-wide blocklist.
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
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


class GmailSkip(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """The two ingest skips that are failures rather than policy (:mod:`gates`).

    **Not a log of what the poller decided.** Recording every skip would be a row per email the
    mailbox receives — newsletters, supplier invoices, password resets — which is more than
    this module is allowed to know, and it would answer speculatively for thousands of messages
    nobody will ever ask about. The nine policy skips are explained on demand instead, by
    re-running the same decision against the one message somebody is looking at.

    These two are here because they fail the opposite test: they are *our* failures, they are
    rare, and they are invisible by construction. A deferral to a colleague's mailbox that then
    stops polling loses the email outright, and a poison message is skipped exactly so that it
    cannot wedge the feed — in both cases the only symptom is an email that is simply not
    there, and nobody knows to go looking for a message they never saw.

    **Ids, a reason and a timestamp. No subject, no participants, no snippet.** The content is
    fetched on demand under the user's own grant when they ask about this message, the same
    rule the rest of the module follows. A retention cron reaps the rows, because a permanent
    record of a transient failure is a log by another name.
    """

    __tablename__ = "gmail_skips"
    __table_args__ = (
        Index(
            "uq_gmail_skips_org_conn_message",
            "org_id",
            "connection_id",
            "gmail_message_id",
            unique=True,
        ),
        # The reaper's whole query: one org's rows older than the window.
        Index("ix_gmail_skips_org_created", "org_id", "created_at"),
    )

    connection_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    gmail_message_id: Mapped[str] = mapped_column(String(64), nullable=False)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: A :class:`~app.integrations.google.gmail.gates.SkipReason` value. Stored as text rather
    #: than a
    #: database enum: the vocabulary belongs to the code that reads it, and adding a reason must
    #: not be a migration.
    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The one or two short strings the reason needs in order to be actionable — a colleague's
    #: address, an exception class. Never message content.
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
