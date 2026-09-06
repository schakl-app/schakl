"""The "connect Microsoft" OAuth client — a *separate* grant from OIDC login (docs/MICROSOFT.md §1).

Modeled on :mod:`app.integrations.google.oauth` (per-org config, an Authlib client cached on a
fingerprint) against the Microsoft identity platform's v2.0 endpoints. Two things differ from
Google and both are decided here rather than discovered later:

- **No ``openid`` on the consent.** Identity comes from Graph's ``/me`` with the access token
  we were just handed, not from an id token. The v2.0 issuer for a ``common``/``organizations``
  registration is per-directory (``…/{tid}/v2.0``) while the discovery document for those
  pseudo-tenants prints a placeholder, so an id-token validator has to be taught to look the
  other way — and a validator taught to look the other way is worse than none. ``/me`` answers
  the same three facts (object id, directory, address) with the credential we store anyway.
- **``offline_access`` is the refresh token.** Without it Microsoft issues none, and a
  connection with no refresh token dies in an hour. It is on every consent, and the callback
  refuses a first connect that came back without one, exactly as the Google flow does.

Refresh tokens are not scope-bound in v2.0: a refresh may ask for any scope the user has
consented to for this registration, so scopes union across consents (incremental) here too.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import decrypt
from app.errors import AppError
from app.integrations.microsoft.models import MicrosoftSettings

#: Graph reports granted scopes by their short names (``Mail.Read``), with or without the
#: ``https://graph.microsoft.com/`` prefix depending on the endpoint; :func:`normalise_scopes`
#: folds both onto the short form these constants use.
GRAPH_SCOPE_PREFIX = "https://graph.microsoft.com/"

#: The refresh token. Never optional (see the module docstring).
SCOPE_OFFLINE = "offline_access"
#: Who the account is — Graph's ``/me``. Every consent carries it.
SCOPE_IDENTITY = "User.Read"
#: Read + write events: the Agenda pulls, approved leave and task blocks push (one-way).
SCOPE_CALENDAR = "Calendars.ReadWrite"
#: Every file the user can reach — their OneDrive *and* the SharePoint libraries they are a
#: member of, which is where an agency's client folders actually live. ``Files.ReadWrite``
#: alone stops at the personal drive.
SCOPE_FILES = "Files.ReadWrite.All"
SCOPE_MAIL = "Mail.Read"

#: Every scope that grants writing events to a calendar.
CALENDAR_WRITE_SCOPES = (SCOPE_CALENDAR,)


def normalise_scopes(raw: str | list[str] | None) -> list[str]:
    """Granted scopes as their short names, deduplicated, in the order they arrived."""
    if raw is None:
        return []
    items = raw.split() if isinstance(raw, str) else list(raw)
    seen: dict[str, None] = {}
    for item in items:
        name = item.strip()
        if not name:
            continue
        if name.startswith(GRAPH_SCOPE_PREFIX):
            name = name[len(GRAPH_SCOPE_PREFIX) :]
        seen.setdefault(name, None)
    return list(seen)


async def microsoft_settings_row(
    session: AsyncSession, org_id: uuid.UUID
) -> MicrosoftSettings | None:
    """The org's stored Microsoft settings. Callers must have bound the RLS GUC to ``org_id``."""
    return await session.scalar(
        select(MicrosoftSettings).where(MicrosoftSettings.org_id == org_id)
    )


def client_credentials(row: MicrosoftSettings | None) -> tuple[str, str]:
    """The app registration to speak to Microsoft as: the org's, else the env fallback."""
    if row is not None and row.client_id and row.client_secret_encrypted:
        return row.client_id, decrypt(row.client_secret_encrypted)
    if settings.microsoft_client_id and settings.microsoft_client_secret:
        return settings.microsoft_client_id, settings.microsoft_client_secret
    raise AppError(
        "microsoft_not_configured", "errors.microsoft_not_configured", status_code=409
    )


def tenant_for(row: MicrosoftSettings | None) -> str:
    """The directory segment of every login URL: the org's, else the instance default."""
    if row is not None and row.tenant_id:
        return row.tenant_id.strip()
    return settings.microsoft_tenant_id or "common"


def oauth_configured(row: MicrosoftSettings | None) -> bool:
    if row is not None and row.client_id and row.client_secret_encrypted:
        return True
    return bool(settings.microsoft_client_id and settings.microsoft_client_secret)


def login_base(tenant: str) -> str:
    return f"{settings.microsoft_login_base_url.rstrip('/')}/{tenant}"


def authorization_endpoint(tenant: str) -> str:
    return f"{login_base(tenant)}/oauth2/v2.0/authorize"


def token_endpoint(tenant: str) -> str:
    return f"{login_base(tenant)}/oauth2/v2.0/token"


def has_calendar_write_scope(scopes: list[str] | None) -> bool:
    granted = set(normalise_scopes(scopes))
    return any(scope in granted for scope in CALENDAR_WRITE_SCOPES)


def missing_files_scope(scopes: list[str] | None) -> bool:
    """True when this connection provably cannot reach OneDrive — *provably* being the point.

    The files scope only rides a consent asked while ``onedrive_enabled`` was already on
    (:func:`scopes_for`), so an org that connected Microsoft for the calendar and switched
    OneDrive on afterwards holds live, ``active`` connections Graph answers 403 for. Deciding
    that here turns an unexplained failure into "reconnect your account". An **empty** scope
    list is not evidence — it means we never recorded what was granted.
    """
    granted = set(normalise_scopes(scopes))
    return bool(granted) and SCOPE_FILES not in granted


def has_mail_scope(scopes: list[str] | None) -> bool:
    return SCOPE_MAIL in set(normalise_scopes(scopes))


def scopes_for(row: MicrosoftSettings | None, *, include_outlook: bool) -> list[str]:
    """The consent this install asks for: identity, the refresh token, and exactly the enabled
    surfaces. Outlook additionally needs the *user's* opt-in (per-user and privacy-sensitive),
    so it only rides along when they ticked the box — reconnecting later adds it."""
    scopes = [SCOPE_OFFLINE, SCOPE_IDENTITY]
    if row is not None and row.calendar_enabled:
        scopes.append(SCOPE_CALENDAR)
    if row is not None and row.onedrive_enabled:
        scopes.append(SCOPE_FILES)
    if include_outlook and row is not None and row.outlook_enabled:
        scopes.append(SCOPE_MAIL)
    return scopes


# --------------------------------------------------------------------------- #
# Authlib client — per org, cached on the connection fingerprint (the google/sso pattern)
# --------------------------------------------------------------------------- #
_client_cache: dict[uuid.UUID, tuple[tuple[str, str, str], Any]] = {}


def _fingerprint(row: MicrosoftSettings | None) -> tuple[str, str, str]:
    # The *encrypted* secret participates so plaintext never keys the cache; Fernet re-encrypts
    # on every save, which only ever over-invalidates.
    tenant = tenant_for(row)
    if row is not None and row.client_id and row.client_secret_encrypted:
        return (row.client_id, row.client_secret_encrypted, tenant)
    return (settings.microsoft_client_id or "", "env", tenant)


def connect_client(org_id: uuid.UUID, row: MicrosoftSettings | None) -> Any:
    """The Authlib client for the connect flow, built at most once per stored config.

    Endpoints are stated rather than discovered: the v2.0 discovery document is one more round
    trip to a host that is itself a setting here, and the two URLs it would hand back are
    fixed by the tenant segment alone.
    """
    fingerprint = _fingerprint(row)
    cached = _client_cache.get(org_id)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    from authlib.integrations.starlette_client import OAuth

    client_id, client_secret = client_credentials(row)
    tenant = tenant_for(row)
    oauth = OAuth()
    oauth.register(
        name="microsoft_connect",
        authorize_url=authorization_endpoint(tenant),
        access_token_url=token_endpoint(tenant),
        client_id=client_id,
        client_secret=client_secret,
        client_kwargs={"scope": " ".join((SCOPE_OFFLINE, SCOPE_IDENTITY))},
    )
    client = oauth.microsoft_connect
    _client_cache[org_id] = (fingerprint, client)
    return client


def invalidate_client(org_id: uuid.UUID) -> None:
    """Drop the cached client after a settings save (belt to the fingerprint's braces)."""
    _client_cache.pop(org_id, None)


def safe_return_path(raw: str | None, fallback: str) -> str:
    """A caller-supplied "send me back here" path, or ``fallback`` if it isn't one — the same
    rule the Google connect flow applies, for the same reason: the value comes off a URL."""
    path = (raw or "").strip()
    if not path.startswith("/"):
        return fallback
    if len(path) > 1 and path[1] in "/\\":
        return fallback
    if "\n" in path or "\r" in path:
        return fallback
    return path
