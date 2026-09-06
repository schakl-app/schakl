"""``microsoft`` core — per-org Microsoft 365 settings and the per-user connection vault.

The two-token rule (docs/GOOGLE.md, docs/MICROSOFT.md §1): OIDC *login* and Graph *API access*
are separate grants. Nothing here touches login — a connection row is the stored result of the
separate "Microsoft koppelen" consent, holding the refresh token **encrypted at rest**
(:mod:`app.core.crypto`, the same Fernet scheme the SSO client secret uses).

``microsoft_settings`` is tenant configuration (Instellingen → Microsoft 365): the agency's own
Entra app registration, which surfaces are on, where client folders live in OneDrive or a
SharePoint library, and how Outlook logging behaves. The client id/secret/tenant fall back to
``SCHAKL_MICROSOFT_CLIENT_ID/SECRET/TENANT_ID`` when the row leaves them empty, so a compose-file
install can configure once via env.
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

from app.core.mailbox.policy import MailApprovalMode, MailThreadFollowup
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: The org-level mail policy is one vocabulary for every mailbox feed (``app/core/mailbox``).
OutlookApprovalMode = MailApprovalMode
OutlookThreadFollowup = MailThreadFollowup


class ConnectionStatus(StrEnum):
    ACTIVE = "active"
    ERROR = "error"  # refresh failed (revoked at Microsoft, rotated encryption key, …)


class MicrosoftSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: the app registration, surface toggles, OneDrive layout, Outlook policy."""

    __tablename__ = "microsoft_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_microsoft_settings_org"),)

    #: The agency's own Entra app registration (docs/MICROSOFT.md §2). Secret write-only.
    client_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The directory the registration signs people in against: ``common``, ``organizations``,
    #: or one directory's id. ``NULL`` means the instance default (``SCHAKL_MICROSOFT_TENANT_ID``).
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    calendar_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    onedrive_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    outlook_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Where client folders live: a drive (the automation account's own OneDrive when empty, or
    #: a SharePoint document library's drive id — the Shared Drive analogue), a parent folder
    #: (an item id) inside it, and an optional template folder a new client folder copies.
    onedrive_drive_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    onedrive_parent_folder_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    onedrive_template_folder_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    onedrive_auto_provision: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: Whose connection background OneDrive work (folder provisioning) acts as — an admin
    #: designates a connected account. Personal calendars/mailboxes never use it.
    automation_connection_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    outlook_approval_mode: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MailApprovalMode.APPROVAL_REQUIRED.value,
        server_default=MailApprovalMode.APPROVAL_REQUIRED.value,
    )
    outlook_thread_followup: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=MailThreadFollowup.INHERIT_PENDING.value,
        server_default=MailThreadFollowup.INHERIT_PENDING.value,
    )
    #: Also ingest colleague-to-colleague mail (off by default: it is high-volume and has no
    #: contact to map from). An internal mail always arrives *pending*, whatever the approval
    #: mode — logging it is the reviewer's call, made by filing it onto a client/project.
    outlook_log_internal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class MicrosoftConnection(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One user's Microsoft 365 grant: the encrypted token pair plus per-surface sync state.

    Raw tokens never leave :mod:`app.integrations.microsoft.client` — every caller asks the
    factory for "a client acting as user X" (docs/MICROSOFT.md §2). ``error_notified_at`` dedups
    the "reconnect your Microsoft account" notification: the owner hears it once per breakage.
    """

    __tablename__ = "microsoft_connections"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id", name="uq_microsoft_connections_org_user"),
        Index("ix_microsoft_connections_org_status", "org_id", "status"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Graph's ``/me`` ``id`` — the directory object id, stable for the account's lifetime.
    microsoft_oid: Mapped[str] = mapped_column(String(64), nullable=False)
    #: The directory the account belongs to, as the consent reported it.
    tenant_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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

    # --- outlook (per-user, opt-in — docs/MICROSOFT.md §6) ---------------------------- #
    outlook_sync_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    #: An Outlook category whose messages are never logged (e.g. "geen-crm") — Outlook's
    #: word for Gmail's label.
    outlook_excluded_category: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: The poll cursor: the ``receivedDateTime`` the last poll reached. Graph's per-folder delta
    #: cannot see a message a rule moved out of the Inbox, so the feed reads the whole mailbox
    #: forward from an instant instead (docs/MICROSOFT.md §6). ``NULL`` until the first poll
    #: baselines — connecting a mailbox is opt-in going forward, never a retroactive import.
    outlook_cursor_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    outlook_last_polled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: When the owner last *asked* for a poll (the "Verversen" button, #341) — the cooldown the
    #: manual refresh is rate-limited against, kept apart from the cron's stamp above.
    outlook_manual_poll_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
