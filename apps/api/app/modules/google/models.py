"""``google`` core — per-org Workspace settings and the per-user connection vault.

The two-token rule (docs/GOOGLE.md): OIDC *login* and Workspace *API access* are separate
grants. Nothing here touches login — a connection row is the stored result of the separate
"Google koppelen" consent, holding the refresh token **encrypted at rest**
(:mod:`app.core.crypto`, the same Fernet scheme the SSO client secret uses).

``google_settings`` is tenant configuration (Instellingen → Google): the install's own OAuth
client (per-agency "Internal" app, which is what makes the restricted Drive/Gmail scopes
usable without Google's CASA assessment), which surfaces are on, and how gmail logging
behaves. The OAuth client id/secret fall back to ``SCHAKL_GOOGLE_CLIENT_ID/SECRET`` when the
row leaves them empty, so a compose-file install can configure once via env.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class GmailApprovalMode(StrEnum):
    """Whether a matched email needs the mailbox owner's approval before it is logged."""

    APPROVAL_REQUIRED = "approval_required"
    AUTO_APPROVE = "auto_approve"


class GmailThreadFollowup(StrEnum):
    """What a follow-up in an already-mapped thread does: inherit mappings, or also auto-log."""

    INHERIT_PENDING = "inherit_pending"
    INHERIT_APPROVE = "inherit_approve"


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    ERROR = "error"  # refresh failed (revoked at Google, rotated encryption key, …)


class GoogleSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: the OAuth client, surface toggles, Drive layout, gmail policy."""

    __tablename__ = "google_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_google_settings_org"),)

    #: The install's own OAuth client (docs/GOOGLE.md §2). Secret write-only through the API.
    client_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)

    calendar_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    drive_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    gmail_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Where client folders live: a Shared Drive, a parent folder inside it, and an optional
    #: template folder whose structure a new client folder copies.
    drive_shared_drive_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    drive_parent_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    drive_template_folder_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    drive_auto_provision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Whose connection background Drive work (folder provisioning) acts as — an admin
    #: designates a connected account. Personal calendars/mailboxes never use it.
    automation_connection_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    gmail_approval_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=GmailApprovalMode.APPROVAL_REQUIRED.value,
        server_default=GmailApprovalMode.APPROVAL_REQUIRED.value,
    )
    gmail_thread_followup: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=GmailThreadFollowup.INHERIT_PENDING.value,
        server_default=GmailThreadFollowup.INHERIT_PENDING.value,
    )


class GoogleConnection(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One user's Workspace grant: the encrypted token pair plus per-surface sync state.

    Raw tokens never leave :mod:`app.modules.google.client` — every caller asks the factory
    for "a client acting as user X" (docs/GOOGLE.md §2). ``error_notified_at`` dedups the
    "reconnect your Google account" notification: the owner hears it once per breakage, not
    once per cron tick.
    """

    __tablename__ = "google_connections"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_google_connections_org_user"),
        Index("ix_google_connections_org_status", "org_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    google_sub: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    scopes: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default="{}"
    )

    refresh_token_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    access_token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=ConnectionStatus.ACTIVE.value,
        server_default=ConnectionStatus.ACTIVE.value,
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- gmail (per-user, opt-in — docs/GOOGLE.md §6) ------------------------------------- #
    gmail_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: A Gmail label name whose messages are never logged (e.g. "geen-crm").
    gmail_excluded_label: Mapped[str | None] = mapped_column(String(128), nullable=True)
    gmail_history_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    gmail_last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
