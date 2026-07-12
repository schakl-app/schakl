"""``interactions`` — contactmomenten: the touchpoint timeline (issue #22, Gmail surface).

One org-scoped table holds every touchpoint with a client: manually logged meetings, phone
calls and notes, and — when the licensed ``google`` module feeds it — auto-matched emails.
The module itself is free: an agency logs contactmomenten by hand without any Google license,
and the gmail poller writes through this module's published ``system`` surface (dependency
direction: google → interactions, never the reverse).

Shape decisions, agreed with the user on the issue:

- **Direct nullable links, not a generic relations table** — an interaction attaches to a
  company and/or project and/or task and/or contact. The links are ``ON DELETE SET NULL``:
  a deleted project must not erase the record that a call happened. When a project/task link
  is set, ``company_id`` is derived from it (when unambiguous) so the client timeline stays
  complete without query-time roll-ups.
- **Emails arrive ``pending``** (per-org configurable): the team sees metadata (participants,
  subject, snippet); the body is only fetched — and visible — after the mailbox owner
  approves. Rejection deletes the row and suppresses the message, so a re-poll never
  resurrects it. Only the mailbox owner may approve/reject/remap a gmail-sourced row —
  ``interactions.interaction.review`` has **no** ``:any`` escape by design.
- **The owner is snapshotted** (``owner_name``, issue #64 rule): a mailbox owner or author
  who later leaves keeps their name on the timeline.
- **``rfc822_message_id`` dedups across mailboxes**: Gmail's message/thread ids are
  per-mailbox, so two connected colleagues on one thread would otherwise both log it. The
  global ``Message-ID`` header keeps the timeline at one entry per email; the first mailbox
  to poll owns it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

ENTITY_TYPE = "interaction"


class InteractionKind(StrEnum):
    EMAIL = "email"
    MEETING = "meeting"
    CALL = "call"
    NOTE = "note"


#: Kinds a person may log by hand; ``email`` rows are only ever written by the gmail feed.
MANUAL_KINDS = (InteractionKind.MEETING, InteractionKind.CALL, InteractionKind.NOTE)


class InteractionStatus(StrEnum):
    PENDING = "pending"  # gmail-sourced, awaiting the mailbox owner's review
    LOGGED = "logged"


class InteractionDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"
    NONE = "none"


class InteractionSource(StrEnum):
    MANUAL = "manual"
    GMAIL = "gmail"


class Interaction(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "interactions"
    __entity_type__ = ENTITY_TYPE
    __table_args__ = (
        Index("ix_interactions_org_occurred", "org_id", "occurred_at"),
        Index("ix_interactions_org_status", "org_id", "status"),
        # Poll idempotency: one row per message per mailbox. Partial — manual rows carry NULLs.
        Index(
            "uq_interactions_org_owner_gmail_message",
            "org_id",
            "owner_user_id",
            "gmail_message_id",
            unique=True,
            postgresql_where=text("gmail_message_id IS NOT NULL"),
        ),
        Index("ix_interactions_org_rfc822", "org_id", "rfc822_message_id"),
        Index("ix_interactions_org_thread", "org_id", "gmail_thread_id"),
    )

    kind: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InteractionStatus.LOGGED.value
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Metadata-first (docs/GOOGLE.md §6): the preview the team sees while an email is pending.
    snippet: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Manual notes at write time; for gmail rows only filled after the owner approves.
    body_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    direction: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InteractionDirection.NONE.value
    )

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # The mailbox owner (gmail) or the author (manual). SET NULL + snapshot, issue #64.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    owner_name: Mapped[str | None] = mapped_column(String(255), nullable=True)

    #: ``[{"email": …, "name": …, "role": "from"|"to"|"cc"}]`` — display data, never authz.
    participants: Mapped[list[Any]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InteractionSource.MANUAL.value
    )
    gmail_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The global RFC 5322 ``Message-ID`` header — dedup key across connected mailboxes.
    rfc822_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deep_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
