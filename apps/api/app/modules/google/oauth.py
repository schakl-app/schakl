"""The "connect Google" OAuth client — a *separate* grant from OIDC login (docs/GOOGLE.md §1).

Modeled on :mod:`app.core.auth.sso` (per-org config, client cached on a fingerprint) but with
the offline-access shape login must never have: ``access_type=offline`` + ``prompt=consent``
so Google returns a refresh token, and ``include_granted_scopes=true`` so a later reconnect
that adds Gmail keeps the Calendar/Drive scopes already granted (incremental authorization).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt
from app.errors import AppError
from app.modules.google.models import GoogleSettings

GOOGLE_DISCOVERY_URL = "https://accounts.google.com/.well-known/openid-configuration"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # noqa: S105 — an endpoint URL, not a secret
GOOGLE_REVOKE_ENDPOINT = "https://oauth2.googleapis.com/revoke"

#: ``openid email`` names the account (sub + address) — no profile, no login semantics.
SCOPE_IDENTITY = ("openid", "email")
#: Read + write events: the Agenda pulls, approved leave pushes (one-way, docs/GOOGLE.md §4).
SCOPE_CALENDAR = "https://www.googleapis.com/auth/calendar.events"
#: Full Drive, not ``drive.file``: browsing *existing* client folders is the whole point.
#: Restricted scope — acceptable only under the per-agency "Internal" OAuth app (§2).
SCOPE_DRIVE = "https://www.googleapis.com/auth/drive"
SCOPE_GMAIL = "https://www.googleapis.com/auth/gmail.readonly"

#: Marketing surfaces (the ``marketing`` module, epic #134) ride this one OAuth client too:
#: read-only Analytics (GA4) and Search Console, plus Google Ads. Ads has **no** read-only
#: scope — ``adwords`` is the only one, and it additionally needs a per-agency developer token
#: to actually call the API (the module presents fine while Ads stays unconfigured). These are
#: added by *incremental* authorization: a connection that already granted Calendar keeps it,
#: ``include_granted_scopes=true`` unions the new marketing scopes on (docs/GOOGLE.md §1).
SCOPE_ANALYTICS = "https://www.googleapis.com/auth/analytics.readonly"
SCOPE_SEARCH_CONSOLE = "https://www.googleapis.com/auth/webmasters.readonly"
SCOPE_ADS = "https://www.googleapis.com/auth/adwords"
#: Every marketing scope, so a caller can ask "does this connection carry any marketing grant?"
MARKETING_SCOPES = (SCOPE_ANALYTICS, SCOPE_SEARCH_CONSOLE, SCOPE_ADS)


async def google_settings_row(
    session: AsyncSession, org_id: uuid.UUID
) -> GoogleSettings | None:
    """The org's stored Google settings. Callers must have bound the RLS GUC to ``org_id``."""
    return await session.scalar(select(GoogleSettings).where(GoogleSettings.org_id == org_id))


def client_credentials(row: GoogleSettings | None) -> tuple[str, str]:
    """The OAuth client to speak to Google as: the org's stored one, else the env fallback."""
    if row is not None and row.client_id and row.client_secret_encrypted:
        return row.client_id, decrypt(row.client_secret_encrypted)
    if settings.google_client_id and settings.google_client_secret:
        return settings.google_client_id, settings.google_client_secret
    raise AppError("google_not_configured", "errors.google_not_configured", status_code=409)


def oauth_configured(row: GoogleSettings | None) -> bool:
    if row is not None and row.client_id and row.client_secret_encrypted:
        return True
    return bool(settings.google_client_id and settings.google_client_secret)


def scopes_for(
    row: GoogleSettings | None,
    *,
    include_gmail: bool,
    include_analytics: bool = False,
    include_search_console: bool = False,
    include_ads: bool = False,
) -> list[str]:
    """The consent this install asks for: identity plus exactly the enabled surfaces.

    Gmail additionally needs the *user's* opt-in (per-user and privacy-sensitive), so it only
    rides along when they ticked the box — reconnecting later adds it incrementally.

    The marketing scopes (``include_analytics`` / ``include_search_console`` / ``include_ads``)
    are requested only when the caller asks — the ``marketing`` module's connect deep-link sets
    the flags for the sources being linked. They are not gated on a ``google_settings`` toggle:
    marketing enablement is a separate module (``org_settings.enabled_modules``), and requesting
    a scope the connection already holds is a no-op thanks to ``include_granted_scopes``.
    """
    scopes = list(SCOPE_IDENTITY)
    if row is not None and row.calendar_enabled:
        scopes.append(SCOPE_CALENDAR)
    if row is not None and row.drive_enabled:
        scopes.append(SCOPE_DRIVE)
    if include_gmail and row is not None and row.gmail_enabled:
        scopes.append(SCOPE_GMAIL)
    if include_analytics:
        scopes.append(SCOPE_ANALYTICS)
    if include_search_console:
        scopes.append(SCOPE_SEARCH_CONSOLE)
    if include_ads:
        scopes.append(SCOPE_ADS)
    return scopes


# --------------------------------------------------------------------------- #
# Authlib client — per org, cached on the connection fingerprint (sso.py pattern)
# --------------------------------------------------------------------------- #
_client_cache: dict[uuid.UUID, tuple[tuple[str, str], Any]] = {}


def _fingerprint(row: GoogleSettings | None) -> tuple[str, str]:
    # The *encrypted* secret participates so plaintext never keys the cache; Fernet re-encrypts
    # on every save, which only ever over-invalidates.
    if row is not None and row.client_id and row.client_secret_encrypted:
        return (row.client_id, row.client_secret_encrypted)
    return (settings.google_client_id or "", "env")


def connect_client(org_id: uuid.UUID, row: GoogleSettings | None) -> Any:
    """The Authlib client for the connect flow, built at most once per stored config."""
    fingerprint = _fingerprint(row)
    cached = _client_cache.get(org_id)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    from authlib.integrations.starlette_client import OAuth

    client_id, client_secret = client_credentials(row)
    oauth = OAuth()
    oauth.register(
        name="google_connect",
        server_metadata_url=GOOGLE_DISCOVERY_URL,
        client_id=client_id,
        client_secret=client_secret,
        client_kwargs={"scope": " ".join(SCOPE_IDENTITY)},
    )
    client = oauth.google_connect
    _client_cache[org_id] = (fingerprint, client)
    return client


def invalidate_client(org_id: uuid.UUID) -> None:
    """Drop the cached client after a settings save (belt to the fingerprint's braces)."""
    _client_cache.pop(org_id, None)
