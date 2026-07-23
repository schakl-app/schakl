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

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.activity import AuditableMixin
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

ENTITY_TYPE = "interaction"


class InteractionKind(StrEnum):
    """The system kinds every org starts with (#174). ``Interaction.kind`` is a free key
    into the org's ``interaction_kinds`` list now, not this enum — the enum survives for the
    protected ``EMAIL`` constant and the seeded defaults."""

    EMAIL = "email"
    ONLINE_MEETING = "online_meeting"
    PHYSICAL_MEETING = "physical_meeting"
    CALL = "call"
    NOTE = "note"


#: The one kind a person may never log by hand — only the gmail feed writes ``email`` rows,
#: and the kind can be relabelled but never deleted or deactivated (#174).
PROTECTED_KIND = InteractionKind.EMAIL.value

#: Seeded per org (lazily, on first use — the leave-types pattern). ``meeting`` split into
#: online/physical (#174); existing rows were remapped to ``physical_meeting`` by migration.
DEFAULT_KINDS: tuple[dict[str, Any], ...] = (
    {
        "key": InteractionKind.EMAIL.value,
        "label_i18n": {"en": "Email", "nl": "E-mail"},
        "position": 10,
    },
    {
        "key": InteractionKind.ONLINE_MEETING.value,
        "label_i18n": {"en": "Online meeting", "nl": "Online afspraak"},
        "position": 20,
    },
    {
        "key": InteractionKind.PHYSICAL_MEETING.value,
        "label_i18n": {"en": "On-site meeting", "nl": "Afspraak op locatie"},
        "position": 30,
    },
    {
        "key": InteractionKind.CALL.value,
        "label_i18n": {"en": "Call", "nl": "Telefoongesprek"},
        "position": 40,
    },
    {
        "key": InteractionKind.NOTE.value,
        "label_i18n": {"en": "Note", "nl": "Notitie"},
        "position": 50,
    },
)


class InteractionKindDef(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A tenant-configurable interaction kind (#174) — the contact-types / leave-types shape:
    ``key + label_i18n + position + active``, CRUD under Instellingen. ``Interaction.kind``
    stores the ``key``; deactivating a kind hides it from the form without touching history."""

    __tablename__ = "interaction_kinds"
    __table_args__ = (UniqueConstraint("org_id", "key", name="uq_interaction_kinds_org_key"),)

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    # Per-locale labels ({"nl": ..., "en": ...}) — tenant data, like custom-field labels.
    label_i18n: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

#: Which activity-log entity type each link FK mirrors onto (#152): a contactmoment's
#: milestones show on the records it hangs on, in the writing transaction (§16).
HOST_ENTITY = {
    "company_id": "company",
    "project_id": "project",
    "task_id": "task",
    "contact_id": "contact",
}


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
    #: A ``.eml`` a person uploaded by hand (#262) — an email row like a gmail one, but the
    #: bytes came from a file, not from a connected mailbox. Distinct from ``MANUAL`` because
    #: the content is a real message (rendered as such, attachments and all) rather than
    #: someone's typed note, and distinct from ``GMAIL`` because there is no mailbox behind
    #: it: no review flow, no thread, no deep link.
    UPLOAD = "upload"


#: The sources whose body *is* an email message: rendered as received (never as markdown),
#: with their attachments. ``MANUAL`` rows carry a person's own note instead.
EMAIL_SOURCES = frozenset({InteractionSource.GMAIL.value, InteractionSource.UPLOAD.value})


class Interaction(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, AuditableMixin, Base):
    __tablename__ = "interactions"
    __entity_type__ = ENTITY_TYPE
    __activity_read_permission__ = "interactions.interaction.read"  # trail read gate (audit F7)
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
        Index("ix_interactions_org_conversation", "org_id", "conversation_id"),
        Index("ix_interactions_org_thread_root", "org_id", "thread_root_id"),
    )

    #: A key into the org's ``interaction_kinds`` (#174) — validated by the service on manual
    #: writes; no FK, so relabelling/removing a kind never rewrites history.
    kind: Mapped[str] = mapped_column(String(50), nullable=False)
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

    #: Users @mentioned in a manual note body (#151), captured structurally like
    #: ``TaskComment.mentioned_user_ids`` (#63): extracted from the ``@[Name](mention:<uuid>)``
    #: markers and validated against org membership by the service.
    mentioned_user_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    #: Contacts @mentioned (#165) — references into the CRM, never notification recipients;
    #: kept parallel to ``mentioned_user_ids`` so the mention fan-out stays unambiguous.
    mentioned_contact_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )

    source: Mapped[str] = mapped_column(
        String(10), nullable=False, default=InteractionSource.MANUAL.value
    )
    gmail_message_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    gmail_thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Gmail-style conversation grouping (#272): a plain (no-FK) id shared by every logged email
    #: row of one thread, so the list folds a conversation to a single row. Only ever set on
    #: **logged, email** rows — a ``NULL`` row is trivially its own singleton group, so nothing
    #: changes for manual/pending rows. Assigned by ``system.resolve_conversation_id``.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    #: The RFC 5322 thread root of an uploaded ``.eml`` (#272): the oldest Message-ID in its
    #: ``References``/``In-Reply-To`` chain, or its own ``rfc822_message_id`` when it starts a
    #: thread. Only set on **upload** rows — gmail rows fold by ``gmail_thread_id`` instead. Two
    #: uploads share a conversation when they share a root (or one references the other's id).
    thread_root_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: The global RFC 5322 ``Message-ID`` header — dedup key across connected mailboxes.
    rfc822_message_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    deep_link: Mapped[str | None] = mapped_column(String(500), nullable=True)
