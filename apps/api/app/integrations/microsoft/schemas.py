"""Pydantic schemas for the microsoft module's settings and connection surfaces."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.core.mailbox.policy import MailApprovalMode, MailThreadFollowup


class MicrosoftSettingsRead(BaseModel):
    client_id: str | None = None
    #: Write-only secret: the API reports configured / not, never the value (SSO pattern).
    client_secret_configured: bool = False
    #: The directory the registration signs in against; ``None`` means the instance default.
    tenant_id: str | None = None
    #: The env vars are set, so the install works without a stored client (fallback).
    env_client_configured: bool = False
    calendar_enabled: bool = False
    onedrive_enabled: bool = False
    outlook_enabled: bool = False
    onedrive_drive_id: str | None = None
    onedrive_parent_folder_id: str | None = None
    onedrive_template_folder_id: str | None = None
    onedrive_auto_provision: bool = False
    automation_connection_user_id: uuid.UUID | None = None
    outlook_approval_mode: MailApprovalMode = MailApprovalMode.APPROVAL_REQUIRED
    outlook_thread_followup: MailThreadFollowup = MailThreadFollowup.INHERIT_PENDING
    outlook_log_internal: bool = False
    #: The redirect URI to register on the Entra app registration (derived, never typed).
    callback_url: str
    #: The notification URL Graph change subscriptions post to (derived, for the admin's proxy
    #: allow-list — the same reason the Google page prints its webhook).
    webhook_url: str
    weak_encryption_key: bool = False


class MicrosoftSettingsWrite(BaseModel):
    client_id: str | None = Field(default=None, max_length=512)
    #: Write-only. Empty / omitted on an update means "keep the stored secret".
    client_secret: str | None = Field(default=None, max_length=1024)
    tenant_id: str | None = Field(default=None, max_length=128)
    calendar_enabled: bool = False
    onedrive_enabled: bool = False
    outlook_enabled: bool = False
    onedrive_drive_id: str | None = Field(default=None, max_length=256)
    onedrive_parent_folder_id: str | None = Field(default=None, max_length=256)
    onedrive_template_folder_id: str | None = Field(default=None, max_length=256)
    onedrive_auto_provision: bool = False
    automation_connection_user_id: uuid.UUID | None = None
    outlook_approval_mode: MailApprovalMode = MailApprovalMode.APPROVAL_REQUIRED
    outlook_thread_followup: MailThreadFollowup = MailThreadFollowup.INHERIT_PENDING
    outlook_log_internal: bool = False


class MicrosoftConnectionRead(BaseModel):
    """The caller's own connection — or the admin list's per-user rows."""

    user_id: uuid.UUID
    email: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    outlook_sync_enabled: bool = False
    outlook_excluded_category: str | None = None
    connected_at: datetime
    last_error: str | None = None


class MyMicrosoftConnectionRead(BaseModel):
    connected: bool = False
    connection: MicrosoftConnectionRead | None = None
    #: Whether the org's app registration is configured at all — the account card's gate.
    configured: bool = False
    #: Which surfaces the org has enabled, so the card can say what connecting grants.
    calendar_enabled: bool = False
    onedrive_enabled: bool = False
    outlook_enabled: bool = False


class MyMicrosoftConnectionUpdate(BaseModel):
    outlook_sync_enabled: bool | None = None
    outlook_excluded_category: str | None = Field(default=None, max_length=128)
