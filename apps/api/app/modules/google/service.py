"""Settings + connections services for the google core (docs/GOOGLE.md §3)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import settings
from app.core.auth.sso import org_base_url
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.google import client as google_client
from app.modules.google.models import GoogleConnection, GoogleSettings
from app.modules.google.oauth import google_settings_row, invalidate_client, oauth_configured
from app.modules.google.schemas import (
    ConnectionRead,
    GoogleSettingsRead,
    GoogleSettingsWrite,
    MyConnectionRead,
    MyConnectionUpdate,
)


def _weak_encryption_key() -> bool:
    default_secret = type(settings).model_fields["secret_key"].default
    return not settings.encryption_key and settings.secret_key == default_secret


def callback_url(org) -> str:
    """What the admin registers on the Google Cloud OAuth client (derived, never typed)."""
    return f"{org_base_url(org)}/api/v1/google/oauth/callback"


class GoogleSettingsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _row(self) -> GoogleSettings | None:
        return await google_settings_row(self.ctx.session, self.ctx.org.id)

    def _read(self, row: GoogleSettings | None) -> GoogleSettingsRead:
        return GoogleSettingsRead(
            client_id=row.client_id if row else None,
            client_secret_configured=bool(row and row.client_secret_encrypted),
            env_client_configured=bool(
                settings.google_client_id and settings.google_client_secret
            ),
            calendar_enabled=bool(row and row.calendar_enabled),
            drive_enabled=bool(row and row.drive_enabled),
            gmail_enabled=bool(row and row.gmail_enabled),
            drive_shared_drive_id=row.drive_shared_drive_id if row else None,
            drive_parent_folder_id=row.drive_parent_folder_id if row else None,
            drive_template_folder_id=row.drive_template_folder_id if row else None,
            drive_auto_provision=bool(row and row.drive_auto_provision),
            automation_connection_user_id=(
                row.automation_connection_user_id if row else None
            ),
            gmail_approval_mode=(row.gmail_approval_mode if row else "approval_required"),
            gmail_thread_followup=(row.gmail_thread_followup if row else "inherit_pending"),
            callback_url=callback_url(self.ctx.org),
            weak_encryption_key=_weak_encryption_key(),
        )

    async def get(self) -> GoogleSettingsRead:
        return self._read(await self._row())

    async def save(self, data: GoogleSettingsWrite) -> GoogleSettingsRead:
        self.ctx.require("google.settings.manage")
        row = await self._row()

        # An empty secret keeps the stored one; a resent identical secret is not a change
        # (the SSO settings rule — Fernet would otherwise re-encrypt on every save).
        secret_encrypted = row.client_secret_encrypted if row else None
        if data.client_secret:
            stored_plain: str | None = None
            if secret_encrypted:
                try:
                    stored_plain = decrypt(secret_encrypted)
                except ValueError:  # rotated key: the stored token is dead anyway
                    stored_plain = None
            if stored_plain != data.client_secret:
                secret_encrypted = encrypt(data.client_secret)

        if data.automation_connection_user_id is not None:
            connection = await google_client.connection_for(
                self.ctx.session, self.ctx.org.id, data.automation_connection_user_id
            )
            if connection is None:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"automation_connection_user_id": "errors.google_not_connected"},
                )

        values = dict(
            client_id=(data.client_id or "").strip() or None,
            client_secret_encrypted=secret_encrypted,
            calendar_enabled=data.calendar_enabled,
            drive_enabled=data.drive_enabled,
            gmail_enabled=data.gmail_enabled,
            drive_shared_drive_id=(data.drive_shared_drive_id or "").strip() or None,
            drive_parent_folder_id=(data.drive_parent_folder_id or "").strip() or None,
            drive_template_folder_id=(data.drive_template_folder_id or "").strip() or None,
            drive_auto_provision=data.drive_auto_provision,
            automation_connection_user_id=data.automation_connection_user_id,
            gmail_approval_mode=data.gmail_approval_mode.value,
            gmail_thread_followup=data.gmail_thread_followup.value,
        )
        if row is None:
            row = GoogleSettings(org_id=self.ctx.org.id, **values)
            self.ctx.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await self.ctx.session.flush()
        invalidate_client(self.ctx.org.id)
        return self._read(row)


class GoogleConnectionsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    def _read(self, row: GoogleConnection) -> ConnectionRead:
        return ConnectionRead(
            user_id=row.user_id,
            email=row.email,
            status=row.status,
            scopes=list(row.scopes or []),
            gmail_sync_enabled=row.gmail_sync_enabled,
            gmail_excluded_label=row.gmail_excluded_label,
            connected_at=row.created_at,
            last_error=row.last_error,
        )

    async def list(self) -> list[ConnectionRead]:
        rows = (
            (
                await self.ctx.session.execute(
                    select(GoogleConnection)
                    .where(GoogleConnection.org_id == self.ctx.org.id)
                    .order_by(GoogleConnection.email)
                )
            )
            .scalars()
            .all()
        )
        return [self._read(row) for row in rows]

    async def me(self) -> MyConnectionRead:
        row = await google_settings_row(self.ctx.session, self.ctx.org.id)
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        return MyConnectionRead(
            connected=connection is not None,
            connection=self._read(connection) if connection else None,
            configured=oauth_configured(row),
            calendar_enabled=bool(row and row.calendar_enabled),
            drive_enabled=bool(row and row.drive_enabled),
            gmail_enabled=bool(row and row.gmail_enabled),
        )

    async def update_me(self, data: MyConnectionUpdate) -> MyConnectionRead:
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None:
            raise AppError(
                "google_not_connected", "errors.google_not_connected", status_code=409
            )
        sent = data.model_dump(exclude_unset=True)
        if "gmail_sync_enabled" in sent and sent["gmail_sync_enabled"] is not None:
            connection.gmail_sync_enabled = bool(sent["gmail_sync_enabled"])
        if "gmail_excluded_label" in sent:
            connection.gmail_excluded_label = (
                (sent["gmail_excluded_label"] or "").strip() or None
            )
        await self.ctx.session.flush()
        return await self.me()

    async def disconnect_me(self) -> None:
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None:
            return
        await google_client.revoke(connection)
        await self.ctx.session.delete(connection)
        await self.ctx.session.flush()

    async def upsert_from_callback(
        self,
        *,
        user_id: uuid.UUID,
        google_sub: str,
        email: str,
        granted_scopes: list[str],
        refresh_token: str | None,
        access_token: str | None,
        expires_at,
    ) -> GoogleConnection:
        """Store the connect flow's result. Google omits the refresh token on a repeat consent
        it considers already granted — keep the stored one; scopes union across consents
        (incremental authorization)."""
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, user_id
        )
        if connection is None:
            if not refresh_token:
                raise AppError(
                    "google_no_refresh_token", "errors.google_no_refresh_token", status_code=409
                )
            connection = GoogleConnection(
                org_id=self.ctx.org.id,
                user_id=user_id,
                google_sub=google_sub,
                email=email,
                scopes=sorted(set(granted_scopes)),
                refresh_token_encrypted=encrypt(refresh_token),
            )
            self.ctx.session.add(connection)
        else:
            connection.google_sub = google_sub
            connection.email = email
            connection.scopes = sorted(set(connection.scopes or []) | set(granted_scopes))
            if refresh_token:
                connection.refresh_token_encrypted = encrypt(refresh_token)
            google_client.clear_connection_error(connection)
        if access_token:
            connection.access_token_encrypted = encrypt(access_token)
            connection.access_token_expires_at = expires_at
        await self.ctx.session.flush()
        return connection
