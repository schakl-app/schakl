"""Pydantic schemas for the google module's settings and connection surfaces."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.modules.google.models import GmailApprovalMode, GmailThreadFollowup


class GoogleSettingsRead(BaseModel):
    client_id: str | None = None
    #: Write-only secret: the API reports configured / not, never the value (SSO pattern).
    client_secret_configured: bool = False
    #: The env vars are set, so the install works without a stored client (fallback).
    env_client_configured: bool = False
    calendar_enabled: bool = False
    drive_enabled: bool = False
    gmail_enabled: bool = False
    drive_shared_drive_id: str | None = None
    drive_parent_folder_id: str | None = None
    drive_template_folder_id: str | None = None
    drive_auto_provision: bool = False
    automation_connection_user_id: uuid.UUID | None = None
    gmail_approval_mode: GmailApprovalMode = GmailApprovalMode.APPROVAL_REQUIRED
    gmail_thread_followup: GmailThreadFollowup = GmailThreadFollowup.INHERIT_PENDING
    #: The redirect URI to register on the Google Cloud OAuth client (derived, never typed).
    callback_url: str
    weak_encryption_key: bool = False


class GoogleSettingsWrite(BaseModel):
    client_id: str | None = Field(default=None, max_length=512)
    #: Write-only. Empty / omitted on an update means "keep the stored secret".
    client_secret: str | None = Field(default=None, max_length=1024)
    calendar_enabled: bool = False
    drive_enabled: bool = False
    gmail_enabled: bool = False
    drive_shared_drive_id: str | None = Field(default=None, max_length=128)
    drive_parent_folder_id: str | None = Field(default=None, max_length=128)
    drive_template_folder_id: str | None = Field(default=None, max_length=128)
    drive_auto_provision: bool = False
    automation_connection_user_id: uuid.UUID | None = None
    gmail_approval_mode: GmailApprovalMode = GmailApprovalMode.APPROVAL_REQUIRED
    gmail_thread_followup: GmailThreadFollowup = GmailThreadFollowup.INHERIT_PENDING


class ConnectionRead(BaseModel):
    """The caller's own connection — or the admin list's per-user rows."""

    user_id: uuid.UUID
    email: str
    status: str
    scopes: list[str] = Field(default_factory=list)
    gmail_sync_enabled: bool = False
    gmail_excluded_label: str | None = None
    connected_at: datetime
    last_error: str | None = None


class MyConnectionRead(BaseModel):
    connected: bool = False
    connection: ConnectionRead | None = None
    #: Whether the org's OAuth client is configured at all — the account card's gate.
    configured: bool = False
    #: Which surfaces the org has enabled, so the card can say what connecting grants.
    calendar_enabled: bool = False
    drive_enabled: bool = False
    gmail_enabled: bool = False


class MyConnectionUpdate(BaseModel):
    gmail_sync_enabled: bool | None = None
    gmail_excluded_label: str | None = Field(default=None, max_length=128)
