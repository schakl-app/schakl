"""The token vault and the "act as user X" client factory (docs/MICROSOFT.md §2/§3).

Every Graph call in the platform goes through :func:`acting_as` — an httpx client that carries
the connection's tokens, refreshes them transparently against the identity platform, and stages
a rotated token back on the row re-encrypted. **Raw tokens never leave this module**: callers get
a client, never a credential, so no call site can stash or log one.

The client is rooted at ``settings.microsoft_graph_base_url`` (``https://graph.microsoft.com/
v1.0`` unless a test stack or a sovereign cloud says otherwise), so call sites spell paths
(``/me/messages``) and never a host — the one place the host is spelled is the setting.

Failure shape: a refresh the identity platform refuses (``invalid_grant`` — the user revoked
access, an admin wiped the grant, the registration's secret expired, or the encryption key
rotated under the stored token) flips the connection to ``error`` and notifies its owner once.
Background sync skips error connections; the owner reconnects from Instellingen → Account.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from authlib.integrations.base_client.errors import OAuthError
from authlib.integrations.httpx_client import AsyncOAuth2Client
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt, encrypt
from app.core.events import SystemContext
from app.core.models import Org
from app.errors import AppError
from app.integrations.microsoft.models import ConnectionStatus, MicrosoftConnection
from app.integrations.microsoft.oauth import (
    client_credentials,
    microsoft_settings_row,
    tenant_for,
    token_endpoint,
)

logger = logging.getLogger("schakl.microsoft")

#: The one ad-hoc notification this module sends: "your Microsoft connection needs attention".
CONNECTION_ERROR_EVENT = "microsoft.connection_error"


async def connection_for(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> MicrosoftConnection | None:
    """The user's connection row (RLS GUC must be bound). Any status — callers decide."""
    return await session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == org_id, MicrosoftConnection.user_id == user_id
        )
    )


async def active_connection_or_409(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> MicrosoftConnection:
    connection = await connection_for(session, org_id, user_id)
    if connection is None:
        raise AppError(
            "microsoft_not_connected", "errors.microsoft_not_connected", status_code=409
        )
    if connection.status != ConnectionStatus.ACTIVE.value:
        raise AppError(
            "microsoft_connection_error", "errors.microsoft_connection_error", status_code=409
        )
    return connection


def _token_dict(connection: MicrosoftConnection) -> dict[str, Any]:
    try:
        refresh_token = decrypt(connection.refresh_token_encrypted)
        access_token = (
            decrypt(connection.access_token_encrypted)
            if connection.access_token_encrypted
            else None
        )
    except ValueError as exc:
        # Rotated encryption key: the stored tokens are dead. Not a 500 — a reconnect prompt.
        raise AppError(
            "microsoft_connection_error", "errors.microsoft_connection_error", status_code=409
        ) from exc
    token: dict[str, Any] = {"refresh_token": refresh_token, "token_type": "Bearer"}
    if access_token:
        token["access_token"] = access_token
        if connection.access_token_expires_at is not None:
            token["expires_at"] = int(connection.access_token_expires_at.timestamp())
    else:
        # Force an immediate refresh: authlib only refreshes when it holds an expired token.
        token["access_token"] = "expired"  # noqa: S105 — a placeholder, not a credential
        token["expires_at"] = 1
    return token


@asynccontextmanager
async def acting_as(
    session: AsyncSession,
    org: Org,
    connection: MicrosoftConnection,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
):
    """An authenticated httpx client for this connection, rooted at the Graph base URL; rotated
    tokens are staged on the connection row and persist with the caller's commit.

    An ``OAuthError`` out of a call means the grant itself died — run it through
    :func:`mark_connection_error` and stop syncing that connection. Request paths (never worker
    jobs) must make the actual Graph calls inside ``ctx.release_db()`` so the awaited round-trips
    don't pin a pool connection (docs/PERFORMANCE.md). Enter ``acting_as`` *first* — it reads
    settings — then release.

    ``transport`` is a test seam and nothing else.
    """
    row = await microsoft_settings_row(session, org.id)
    client_id, client_secret = client_credentials(row)

    async def _update_token(
        token: dict[str, Any],
        refresh_token: str | None = None,
        access_token: str | None = None,  # noqa: ARG001 — authlib's signature
    ) -> None:
        # Memory only — no flush. The dirty attributes flush with the caller's own commit;
        # SQL here would check a connection back out without the RLS GUC (docs/PERFORMANCE.md).
        connection.access_token_encrypted = encrypt(token["access_token"])
        expires_at = token.get("expires_at")
        connection.access_token_expires_at = (
            datetime.fromtimestamp(int(expires_at), tz=UTC) if expires_at else None
        )
        # Microsoft rotates the refresh token on every refresh; keep the newest.
        if token.get("refresh_token"):
            connection.refresh_token_encrypted = encrypt(token["refresh_token"])
        elif refresh_token:
            connection.refresh_token_encrypted = encrypt(refresh_token)

    client = AsyncOAuth2Client(
        client_id=client_id,
        client_secret=client_secret,
        token=_token_dict(connection),
        token_endpoint=token_endpoint(tenant_for(row)),
        # The scopes already granted, so a refresh asks for exactly them: v2.0 refreshes are
        # not scope-bound, and an unscoped refresh answers with the registration's default set.
        scope=" ".join(connection.scopes or []) or None,
        update_token=_update_token,
        timeout=20.0,
        base_url=settings.microsoft_graph_base_url,
        # Outlook dates in UTC, whatever the mailbox's own display zone: every instant the sync
        # stores is then one parse away, and a wall clock is never mistaken for one.
        headers={"Prefer": 'outlook.timezone="UTC"'},
        **({"transport": transport} if transport is not None else {}),
    )
    try:
        yield client
    finally:
        await client.aclose()


async def mark_connection_error(
    session: AsyncSession, org: Org, connection: MicrosoftConnection, message: str
) -> None:
    """Flag a dead grant and tell its owner once — not once per cron tick."""
    now = datetime.now(UTC)
    connection.status = ConnectionStatus.ERROR.value
    connection.last_error = message[:500]
    if connection.error_since is None:
        connection.error_since = now
    if connection.error_notified_at is None:
        from app.modules.notifications.service import NotificationService

        await NotificationService(SystemContext(org=org, session=session)).ingest(
            CONNECTION_ERROR_EVENT,
            "microsoft_connection",
            connection.id,
            {
                "email": connection.email,
                "_recipients": [connection.user_id],
                "_dedup_key": f"microsoft-conn-error:{connection.id}",
            },
        )
        connection.error_notified_at = now
    await session.flush()


def clear_connection_error(connection: MicrosoftConnection) -> None:
    """A successful reconnect wipes the error state (and re-arms the one-shot notification)."""
    connection.status = ConnectionStatus.ACTIVE.value
    connection.last_error = None
    connection.error_since = None
    connection.error_notified_at = None


async def is_oauth_error(exc: Exception) -> bool:
    return isinstance(exc, OAuthError)


# --------------------------------------------------------------------------- #
# Reading what Graph actually said
# --------------------------------------------------------------------------- #
#: Graph's codes for "that thing is not there" — a message deleted between listing and fetch, a
#: folder that was moved, an event the organiser removed.
_NOT_FOUND_CODES = frozenset(
    {"itemNotFound", "ErrorItemNotFound", "ResourceNotFound", "ErrorInvalidIdMalformed"}
)
#: The delta cursor died: re-baseline, never loop.
_SYNC_STATE_CODES = frozenset(
    {"SyncStateNotFound", "SyncStateInvalid", "ErrorInvalidSyncStateData"}
)
#: The bearer is valid but was minted without the scope this call needs — that *is* a reconnect.
_SCOPE_CODES = frozenset({"ErrorAccessDenied", "accessDenied", "Authorization_RequestDenied"})


@dataclass(frozen=True)
class GraphApiError:
    """Graph's own account of a failed call, pulled out of the ``{"error": {...}}`` body.

    ``httpx``'s ``HTTPStatusError`` stringifies to the status line and the URL only, so a call
    site that logs ``str(exc)`` throws away the diagnosis: a 403 reads the same whether the
    token lacks a scope, the account cannot see the item, or the registration lost an admin
    grant. Graph names the cause in ``error.code`` and the human sentence in ``error.message``.
    """

    status_code: int
    code: str | None
    message: str

    @property
    def not_found(self) -> bool:
        return self.status_code == 404 or self.code in _NOT_FOUND_CODES

    @property
    def sync_state_lost(self) -> bool:
        return self.status_code == 410 or self.code in _SYNC_STATE_CODES

    @property
    def forbidden(self) -> bool:
        return self.status_code in (401, 403)

    @property
    def scope_insufficient(self) -> bool:
        return self.forbidden and self.code in _SCOPE_CODES

    @property
    def throttled(self) -> bool:
        return self.status_code == 429

    def __str__(self) -> str:
        return f"{self.status_code} {self.code or ''}: {self.message}".strip()


def describe_api_error(exc: Exception) -> GraphApiError | None:
    """Graph's error body, or ``None`` when this isn't an HTTP error from Graph."""
    response = getattr(exc, "response", None)
    if not isinstance(exc, httpx.HTTPStatusError) or response is None:
        return None
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    if not isinstance(error, dict):
        error = {}
    return GraphApiError(
        status_code=response.status_code,
        code=str(error["code"]) if error.get("code") else None,
        message=str(error.get("message") or response.text[:500]),
    )


async def registration_hint(session: AsyncSession, org_id: uuid.UUID) -> str:
    """Which app registration this org speaks to Microsoft as, for a failure log line — an org
    that never filled in Instellingen → Microsoft 365 silently rides the instance env client."""
    row = await microsoft_settings_row(session, org_id)
    if row is not None and row.client_id and row.client_secret_encrypted:
        return f"org registration {row.client_id} (tenant {tenant_for(row)})"
    if settings.microsoft_client_id:
        return f"instance env registration {settings.microsoft_client_id}"
    return "no registration configured"
