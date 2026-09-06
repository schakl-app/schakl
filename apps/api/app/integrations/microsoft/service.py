"""Settings + connections services for the microsoft core (docs/MICROSOFT.md §3)."""

from __future__ import annotations

import uuid

from sqlalchemy import select

from app.config import settings
from app.core.auth.sso import org_base_url
from app.core.crypto import decrypt, encrypt
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.microsoft import client as ms_client
from app.integrations.microsoft.models import MicrosoftConnection, MicrosoftSettings
from app.integrations.microsoft.oauth import (
    invalidate_client,
    microsoft_settings_row,
    normalise_scopes,
    oauth_configured,
)
from app.integrations.microsoft.schemas import (
    MicrosoftConnectionRead,
    MicrosoftSettingsRead,
    MicrosoftSettingsWrite,
    MyMicrosoftConnectionRead,
    MyMicrosoftConnectionUpdate,
)


def _weak_encryption_key() -> bool:
    default_secret = type(settings).model_fields["secret_key"].default
    return not settings.encryption_key and settings.secret_key == default_secret


def callback_url(org) -> str:
    """What the admin registers on the Entra app registration (derived, never typed)."""
    return f"{org_base_url(org)}/api/v1/microsoft/oauth/callback"


def webhook_url(org) -> str:
    """Where Graph change notifications land — printed so a proxy in front of the host can be
    told to let Microsoft's servers through."""
    return f"{org_base_url(org)}/api/v1/microsoft/calendar/webhook"


class MicrosoftSettingsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _row(self) -> MicrosoftSettings | None:
        return await microsoft_settings_row(self.ctx.session, self.ctx.org.id)

    def _read(self, row: MicrosoftSettings | None) -> MicrosoftSettingsRead:
        return MicrosoftSettingsRead(
            client_id=row.client_id if row else None,
            client_secret_configured=bool(row and row.client_secret_encrypted),
            tenant_id=row.tenant_id if row else None,
            env_client_configured=bool(
                settings.microsoft_client_id and settings.microsoft_client_secret
            ),
            calendar_enabled=bool(row and row.calendar_enabled),
            onedrive_enabled=bool(row and row.onedrive_enabled),
            outlook_enabled=bool(row and row.outlook_enabled),
            onedrive_drive_id=row.onedrive_drive_id if row else None,
            onedrive_parent_folder_id=row.onedrive_parent_folder_id if row else None,
            onedrive_template_folder_id=row.onedrive_template_folder_id if row else None,
            onedrive_auto_provision=bool(row and row.onedrive_auto_provision),
            automation_connection_user_id=(row.automation_connection_user_id if row else None),
            outlook_approval_mode=(row.outlook_approval_mode if row else "approval_required"),
            outlook_thread_followup=(row.outlook_thread_followup if row else "inherit_pending"),
            outlook_log_internal=bool(row and row.outlook_log_internal),
            callback_url=callback_url(self.ctx.org),
            webhook_url=webhook_url(self.ctx.org),
            weak_encryption_key=_weak_encryption_key(),
        )

    async def get(self) -> MicrosoftSettingsRead:
        return self._read(await self._row())

    async def save(self, data: MicrosoftSettingsWrite) -> MicrosoftSettingsRead:
        self.ctx.require("microsoft.settings.manage")
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
            connection = await ms_client.connection_for(
                self.ctx.session, self.ctx.org.id, data.automation_connection_user_id
            )
            if connection is None:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"automation_connection_user_id": "errors.microsoft_not_connected"},
                )

        values = dict(
            client_id=(data.client_id or "").strip() or None,
            client_secret_encrypted=secret_encrypted,
            tenant_id=(data.tenant_id or "").strip() or None,
            calendar_enabled=data.calendar_enabled,
            onedrive_enabled=data.onedrive_enabled,
            outlook_enabled=data.outlook_enabled,
            onedrive_drive_id=(data.onedrive_drive_id or "").strip() or None,
            onedrive_parent_folder_id=(data.onedrive_parent_folder_id or "").strip() or None,
            onedrive_template_folder_id=(data.onedrive_template_folder_id or "").strip() or None,
            onedrive_auto_provision=data.onedrive_auto_provision,
            automation_connection_user_id=data.automation_connection_user_id,
            outlook_approval_mode=data.outlook_approval_mode.value,
            outlook_thread_followup=data.outlook_thread_followup.value,
            outlook_log_internal=data.outlook_log_internal,
        )
        if row is None:
            row = MicrosoftSettings(org_id=self.ctx.org.id, **values)
            self.ctx.session.add(row)
        else:
            for key, value in values.items():
                setattr(row, key, value)
        await self.ctx.session.flush()
        invalidate_client(self.ctx.org.id)
        return self._read(row)


class MicrosoftConnectionsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    def _read(self, row: MicrosoftConnection) -> MicrosoftConnectionRead:
        return MicrosoftConnectionRead(
            user_id=row.user_id,
            email=row.email,
            status=row.status,
            scopes=list(row.scopes or []),
            outlook_sync_enabled=row.outlook_sync_enabled,
            outlook_excluded_category=row.outlook_excluded_category,
            connected_at=row.created_at,
            last_error=row.last_error,
        )

    async def list(self) -> list[MicrosoftConnectionRead]:
        rows = (
            (
                await self.ctx.session.execute(
                    select(MicrosoftConnection)
                    .where(MicrosoftConnection.org_id == self.ctx.org.id)
                    .order_by(MicrosoftConnection.email)
                )
            )
            .scalars()
            .all()
        )
        return [self._read(row) for row in rows]

    async def me(self) -> MyMicrosoftConnectionRead:
        row = await microsoft_settings_row(self.ctx.session, self.ctx.org.id)
        connection = await ms_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        return MyMicrosoftConnectionRead(
            connected=connection is not None,
            connection=self._read(connection) if connection else None,
            configured=oauth_configured(row),
            calendar_enabled=bool(row and row.calendar_enabled),
            onedrive_enabled=bool(row and row.onedrive_enabled),
            outlook_enabled=bool(row and row.outlook_enabled),
        )

    async def update_me(self, data: MyMicrosoftConnectionUpdate) -> MyMicrosoftConnectionRead:
        connection = await ms_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None:
            raise AppError(
                "microsoft_not_connected", "errors.microsoft_not_connected", status_code=409
            )
        sent = data.model_dump(exclude_unset=True)
        if "outlook_sync_enabled" in sent and sent["outlook_sync_enabled"] is not None:
            connection.outlook_sync_enabled = bool(sent["outlook_sync_enabled"])
        if "outlook_excluded_category" in sent:
            connection.outlook_excluded_category = (
                (sent["outlook_excluded_category"] or "").strip() or None
            )
        await self.ctx.session.flush()
        return await self.me()

    async def disconnect_me(self) -> None:
        """Forget the grant. Microsoft's v2.0 identity platform offers no token-revocation
        endpoint for an OAuth client, so the stored tokens are deleted here and the user is told
        (on the card) where to revoke the registration's access on the Microsoft side."""
        connection = await ms_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None:
            return
        await self.ctx.session.delete(connection)
        await self.ctx.session.flush()

    async def upsert_from_callback(
        self,
        *,
        user_id: uuid.UUID,
        microsoft_oid: str,
        tenant_id: str | None,
        email: str,
        granted_scopes: list[str],
        refresh_token: str | None,
        access_token: str | None,
        expires_at,
    ) -> MicrosoftConnection:
        """Store the connect flow's result. A repeat consent that comes back without a refresh
        token keeps the stored one; scopes union across consents (incremental authorization)."""
        granted = normalise_scopes(granted_scopes)
        connection = await ms_client.connection_for(self.ctx.session, self.ctx.org.id, user_id)
        if connection is None:
            if not refresh_token:
                raise AppError(
                    "microsoft_no_refresh_token",
                    "errors.microsoft_no_refresh_token",
                    status_code=409,
                )
            connection = MicrosoftConnection(
                org_id=self.ctx.org.id,
                user_id=user_id,
                microsoft_oid=microsoft_oid,
                tenant_id=tenant_id,
                email=email,
                scopes=sorted(set(granted)),
                refresh_token_encrypted=encrypt(refresh_token),
            )
            self.ctx.session.add(connection)
        else:
            connection.microsoft_oid = microsoft_oid
            connection.tenant_id = tenant_id
            connection.email = email
            connection.scopes = sorted(set(connection.scopes or []) | set(granted))
            if refresh_token:
                connection.refresh_token_encrypted = encrypt(refresh_token)
            ms_client.clear_connection_error(connection)
        if access_token:
            connection.access_token_encrypted = encrypt(access_token)
            connection.access_token_expires_at = expires_at
        await self.ctx.session.flush()
        return connection
