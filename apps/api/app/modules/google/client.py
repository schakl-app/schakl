"""The token vault and the "act as user X" client factory (docs/GOOGLE.md §2/§3).

Every Google API call in the platform goes through :func:`acting_as` — an httpx client that
carries the connection's tokens, refreshes them transparently, and persists a rotated access
token back to the row re-encrypted. **Raw tokens never leave this module**: callers get a
client, never a credential, so no call site can stash or log one.

Failure shape: a refresh that Google refuses (``invalid_grant`` — the user revoked access, an
admin wiped the grant, or the encryption key rotated under the stored token) flips the
connection to ``error`` and notifies its owner once. Background sync skips error connections;
the owner reconnects from Instellingen → Account.
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
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.google.oauth import (
    GOOGLE_REVOKE_ENDPOINT,
    GOOGLE_TOKEN_ENDPOINT,
    client_credentials,
    google_settings_row,
)

logger = logging.getLogger("schakl.google")

#: The one ad-hoc notification this module sends: "your Google connection needs attention".
CONNECTION_ERROR_EVENT = "google.connection_error"


async def connection_for(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> GoogleConnection | None:
    """The user's connection row (RLS GUC must be bound). Any status — callers decide."""
    return await session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == org_id, GoogleConnection.user_id == user_id
        )
    )


async def active_connection_or_409(
    session: AsyncSession, org_id: uuid.UUID, user_id: uuid.UUID
) -> GoogleConnection:
    connection = await connection_for(session, org_id, user_id)
    if connection is None:
        raise AppError("google_not_connected", "errors.google_not_connected", status_code=409)
    if connection.status != ConnectionStatus.ACTIVE.value:
        raise AppError(
            "google_connection_error", "errors.google_connection_error", status_code=409
        )
    return connection


def _token_dict(connection: GoogleConnection) -> dict[str, Any]:
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
            "google_connection_error", "errors.google_connection_error", status_code=409
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
async def acting_as(session: AsyncSession, org: Org, connection: GoogleConnection):
    """An authenticated httpx client for this connection; rotated tokens are staged on the
    connection row and persist with the caller's commit.

    Callers treat it as a plain ``httpx.AsyncClient`` against ``www.googleapis.com``. An
    ``OAuthError`` out of a call means the grant itself died — run it through
    :func:`mark_connection_error` and stop syncing that connection.

    Request paths (never worker jobs) must make the actual Google calls inside
    ``ctx.release_db()`` so the awaited round-trips don't pin a pool connection
    (docs/PERFORMANCE.md). Enter ``acting_as`` *first* — it reads settings — then release.
    """
    row = await google_settings_row(session, org.id)
    client_id, client_secret = client_credentials(row)

    async def _update_token(
        token: dict[str, Any],
        refresh_token: str | None = None,
        access_token: str | None = None,  # noqa: ARG001 — authlib's signature
    ) -> None:
        # Memory only — no flush. Authlib fires this mid-call, and request paths make their
        # Google calls with the session's pool connection released (``ctx.release_db()``,
        # docs/PERFORMANCE.md): SQL here would check a connection back out without the RLS
        # GUC and the UPDATE would match nothing. The dirty attributes flush with the
        # caller's own commit — which is also all the durability the old flush ever had.
        connection.access_token_encrypted = encrypt(token["access_token"])
        expires_at = token.get("expires_at")
        connection.access_token_expires_at = (
            datetime.fromtimestamp(int(expires_at), tz=UTC) if expires_at else None
        )
        if token.get("refresh_token"):
            connection.refresh_token_encrypted = encrypt(token["refresh_token"])
        elif refresh_token:
            connection.refresh_token_encrypted = encrypt(refresh_token)

    client = AsyncOAuth2Client(
        client_id=client_id,
        client_secret=client_secret,
        token=_token_dict(connection),
        token_endpoint=GOOGLE_TOKEN_ENDPOINT,
        update_token=_update_token,
        timeout=20.0,
    )
    try:
        yield client
    finally:
        await client.aclose()


async def mark_connection_error(
    session: AsyncSession, org: Org, connection: GoogleConnection, message: str
) -> None:
    """Flag a dead grant and tell its owner once — not once per cron tick.

    Runs in the caller's transaction like every write; the notification commits with the
    status flip (the notifications service is transaction-safe by design).
    """
    now = datetime.now(UTC)
    connection.status = ConnectionStatus.ERROR.value
    connection.last_error = message[:500]
    if connection.error_since is None:
        connection.error_since = now
    if connection.error_notified_at is None:
        from app.modules.notifications.service import NotificationService

        await NotificationService(SystemContext(org=org, session=session)).ingest(
            CONNECTION_ERROR_EVENT,
            "google_connection",
            connection.id,
            {
                "email": connection.email,
                "_recipients": [connection.user_id],
                "_dedup_key": f"google-conn-error:{connection.id}",
            },
        )
        connection.error_notified_at = now
    await session.flush()


def clear_connection_error(connection: GoogleConnection) -> None:
    """A successful reconnect wipes the error state (and re-arms the one-shot notification)."""
    connection.status = ConnectionStatus.ACTIVE.value
    connection.last_error = None
    connection.error_since = None
    connection.error_notified_at = None


async def is_oauth_error(exc: Exception) -> bool:
    return isinstance(exc, OAuthError)


# --------------------------------------------------------------------------- #
# Reading what Google actually said
# --------------------------------------------------------------------------- #
#: Google's machine-readable reason for "the API is off in this Cloud project". Nothing the
#: user can do in-app fixes it — reconnecting mints the same token against the same project.
_API_DISABLED_REASONS = frozenset({"SERVICE_DISABLED", "accessNotConfigured"})
#: The bearer is valid but was minted without the scope this call needs — that *is* a reconnect.
_SCOPE_REASONS = frozenset(
    {"ACCESS_TOKEN_SCOPE_INSUFFICIENT", "insufficientPermissions", "insufficientScopes"}
)


@dataclass(frozen=True)
class GoogleApiError:
    """Google's own account of a failed REST call, pulled out of the error body.

    ``httpx``'s ``HTTPStatusError`` stringifies to the status line and the URL only, so a call
    site that logs ``str(exc)`` throws away the entire diagnosis: a 403 reads the same whether
    the API is disabled, the token is under-scoped, or the account simply has no access. The
    body carries a ``reason`` naming which, and a ``message`` that names the Cloud **project**.
    """

    status_code: int
    #: ``error.details[].reason`` / ``error.errors[].reason`` — the machine-readable cause.
    reason: str | None
    #: ``error.status`` — the canonical code (``PERMISSION_DENIED``, ``RESOURCE_EXHAUSTED``, …).
    status: str | None
    #: ``error.message`` — Google's own sentence. Log it verbatim; it names the project number.
    message: str

    @property
    def api_disabled(self) -> bool:
        return self.reason in _API_DISABLED_REASONS

    @property
    def scope_insufficient(self) -> bool:
        return self.reason in _SCOPE_REASONS

    def __str__(self) -> str:
        return f"{self.status_code} {self.status or ''} {self.reason or ''}: {self.message}".strip()


def describe_api_error(exc: Exception) -> GoogleApiError | None:
    """Google's error body, or ``None`` when this isn't an HTTP error from Google."""
    response = getattr(exc, "response", None)
    if not isinstance(exc, httpx.HTTPStatusError) or response is None:
        return None
    try:
        error = (response.json() or {}).get("error") or {}
    except ValueError:
        error = {}
    if not isinstance(error, dict):
        error = {}
    reason: str | None = None
    for detail in error.get("details") or []:
        if isinstance(detail, dict) and detail.get("reason"):
            reason = str(detail["reason"])
            break
    if reason is None:
        for legacy in error.get("errors") or []:  # the older Google JSON error shape
            if isinstance(legacy, dict) and legacy.get("reason"):
                reason = str(legacy["reason"])
                break
    return GoogleApiError(
        status_code=response.status_code,
        reason=reason,
        status=str(error["status"]) if error.get("status") else None,
        message=str(error.get("message") or response.text[:500]),
    )


async def oauth_client_hint(session: AsyncSession, org_id: uuid.UUID) -> str:
    """Which OAuth client this org speaks to Google as, for a failure log line.

    The APIs a call needs must be enabled in the Cloud project **of this client** — and an org
    that never filled in Instellingen → Google silently rides the instance-wide env client, whose
    project is somebody else's. That mismatch is invisible in every other symptom, so name the
    client (public — it rides every consent URL; its leading digits are the project number).
    """
    row = await google_settings_row(session, org_id)
    if row is not None and row.client_id and row.client_secret_encrypted:
        return f"org client {row.client_id}"
    if settings.google_client_id:
        return f"instance env client {settings.google_client_id}"
    return "no client configured"


async def revoke(connection: GoogleConnection) -> None:
    """Best-effort revocation at Google on disconnect — local deletion must not depend on it."""
    try:
        token = decrypt(connection.refresh_token_encrypted)
    except ValueError:
        return  # rotated key: nothing usable to revoke
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(GOOGLE_REVOKE_ENDPOINT, data={"token": token})
    except httpx.HTTPError:
        logger.warning("Google token revocation failed; deleting the connection anyway")
