"""Per-org OIDC / SSO configuration (issue #76).

The identity provider is **tenant configuration, not instance configuration**: the
``SCHAKL_OIDC_*`` env vars are retired and every field lives on the org-scoped, RLS-forced
``org_auth_settings`` row — client id, discovery URL, the ``enabled``/``enforced`` toggles, and
the client secret **encrypted at rest** (:mod:`app.core.crypto`, the same Fernet scheme the
notification channels and the e-mail transport use). Enabling, disabling or enforcing SSO is a
settings write, never a restart.

Three consumers resolve the config **at request time** from the org the hostname resolves to:

* the runtime OIDC routes (:mod:`app.core.auth.oidc`) — mounted unconditionally, they answer
  "not configured" per org instead of existing per instance;
* the local-login guard (:func:`require_local_login`) — ``enforced`` refuses password flows for
  that org only, with ``SCHAKL_FORCE_LOCAL_LOGIN=true`` as the operator break-glass
  (docs/SSO.md);
* the login page's SSO button (``/meta/modules``) — per-org, so it can never advertise a flow
  the org has not configured.

The Authlib client is cached per org keyed by a fingerprint of the connection fields, so a save
invalidates it implicitly (a changed config can never reuse a stale client) and explicitly
(:func:`invalidate_client`). Discovery metadata is therefore fetched once per config, not per
login.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import Request
from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.crypto import decrypt
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.models import Org
from app.db import Base, async_session_maker, set_current_org
from app.errors import AppError

# --------------------------------------------------------------------------- #
# Model
# --------------------------------------------------------------------------- #


class OrgAuthSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One row per org: how (and whether) this tenant federates login to an IdP.

    ``oidc_client_secret_encrypted`` is write-only through the API: it accepts a new value and
    reports only "configured / not configured" — the secret never leaves the server.
    ``oidc_tested_at`` records a successful discovery test of the *current* connection fields;
    it is cleared whenever they change, and ``oidc_enforced`` cannot be stored without it —
    enforcing an untested config is instant lockout (the #75 class of failure).
    """

    __tablename__ = "org_auth_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_org_auth_settings_org"),)

    oidc_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    oidc_enforced: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Shown to users on the login button ("Inloggen met <name>").
    oidc_name: Mapped[str] = mapped_column(
        String(64), nullable=False, default="SSO", server_default="SSO"
    )
    oidc_discovery_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    oidc_client_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    oidc_client_secret_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Role key granted by JIT provisioning (a tenant role key, usually a system role).
    oidc_default_role: Mapped[str] = mapped_column(
        String(64), nullable=False, default="member", server_default="member"
    )
    oidc_auto_provision_membership: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    oidc_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


# --------------------------------------------------------------------------- #
# Config resolution
# --------------------------------------------------------------------------- #
async def sso_row(session: AsyncSession, org_id: uuid.UUID) -> OrgAuthSettings | None:
    """The org's stored SSO settings. Callers must have bound the RLS GUC to ``org_id``."""
    return await session.scalar(
        select(OrgAuthSettings).where(OrgAuthSettings.org_id == org_id)
    )


def sso_configured(row: OrgAuthSettings | None) -> bool:
    """Is SSO actually usable for this org? Enabled **and** every connection field present.

    One gate for the routes, the login button and the meta flag (the issue #6 invariant,
    now per org): the button can never point at a flow that would refuse.
    """
    return bool(
        row is not None
        and row.oidc_enabled
        and row.oidc_discovery_url
        and row.oidc_client_id
        and row.oidc_client_secret_encrypted
    )


def local_login_enabled_for(row: OrgAuthSettings | None) -> bool:
    """Password login is on unless this org *enforces* OIDC — with an env break-glass.

    ``SCHAKL_FORCE_LOCAL_LOGIN=true`` re-enables local login regardless of the stored flag, so
    a broken IdP (or a mis-flipped toggle) never locks the tenant out of its own instance
    (docs/SSO.md).
    """
    if settings.force_local_login:
        return True
    return row is None or not row.oidc_enforced


async def require_local_login(request: Request) -> None:
    """Runtime guard on the password-based auth flows (login, register, reset, verify).

    A dependency, not a mount-time branch (issue #76): enforcement is per-org tenant data, so
    the same mounted route refuses on an enforced org and works on any other. ``/auth/logout``
    always passes — ending a session must work however it began.
    """
    if request.url.path.endswith("/logout"):
        return
    if settings.force_local_login:
        return
    from app.core.tenancy import request_hostname, resolve_org

    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            # No org, nothing to enforce; the request fails on its own terms downstream.
            return
        await set_current_org(session, org.id)
        row = await sso_row(session, org.id)
    if not local_login_enabled_for(row):
        raise AppError(
            "local_login_disabled", "auth.local_login_disabled", status_code=403
        )


# --------------------------------------------------------------------------- #
# Callback URL — derived, never configured
# --------------------------------------------------------------------------- #
def org_base_url(org: Org) -> str:
    """The address users reach this org on: its verified custom domain, else slug host."""
    if org.custom_domain and org.custom_domain_verified_at:
        return f"https://{org.custom_domain}"
    return f"https://{org.slug}.{settings.base_domain}"


def callback_url(org: Org) -> str:
    """What the admin registers at the IdP. The runtime value is request-derived
    (``request.url_for("oidc_callback")``, docs/SSO.md); this is the same URL built from the
    org's stored domain so the settings page can display it before any SSO request exists."""
    return f"{org_base_url(org)}/api/v1/auth/oidc/callback"


# --------------------------------------------------------------------------- #
# Authlib client — per org, cached on the connection fingerprint
# --------------------------------------------------------------------------- #
_client_cache: dict[uuid.UUID, tuple[tuple[str, str, str], Any]] = {}


def _fingerprint(row: OrgAuthSettings) -> tuple[str, str, str]:
    # The *encrypted* secret participates so plaintext never sits in the cache key. Fernet
    # re-encrypts to a new token on every save, which only ever over-invalidates.
    return (
        row.oidc_discovery_url or "",
        row.oidc_client_id or "",
        row.oidc_client_secret_encrypted or "",
    )


def oauth_client(row: OrgAuthSettings) -> Any:
    """The Authlib client for this org's stored config, built at most once per config.

    Keyed by a fingerprint of the connection fields, so a stale client can never survive a
    save — even without the explicit :func:`invalidate_client` the settings service also calls.
    """
    fingerprint = _fingerprint(row)
    cached = _client_cache.get(row.org_id)
    if cached is not None and cached[0] == fingerprint:
        return cached[1]

    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name="sso",
        server_metadata_url=row.oidc_discovery_url,
        client_id=row.oidc_client_id,
        client_secret=decrypt(row.oidc_client_secret_encrypted or ""),
        client_kwargs={"scope": "openid email profile"},
    )
    client = oauth.sso
    _client_cache[row.org_id] = (fingerprint, client)
    return client


def invalidate_client(org_id: uuid.UUID) -> None:
    """Drop the cached client after a settings save (belt to the fingerprint's braces)."""
    _client_cache.pop(org_id, None)


# --------------------------------------------------------------------------- #
# Discovery validation — the "Test connection" seam
# --------------------------------------------------------------------------- #
#: What a usable OIDC discovery document must at least declare for the code flow.
_REQUIRED_METADATA = ("issuer", "authorization_endpoint", "token_endpoint", "jwks_uri")


def valid_http_url(url: str) -> bool:
    """A syntactic gate for the discovery URL. ``http`` stays allowed on purpose: self-hosted
    installs commonly run their IdP (Keycloak, Authentik) on the same private network."""
    parts = urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname)


async def fetch_discovery(url: str) -> dict:
    """Fetch and validate the discovery document. Raises :class:`ValueError` with a readable
    reason on anything short of a well-formed OIDC configuration.

    Synchronous network I/O is fine here: this runs only from the explicit admin "Test
    connection" action, never on a login path (the Authlib client does its own fetch, cached).
    """
    if not valid_http_url(url):
        raise ValueError("not an http(s) URL")
    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        response = await client.get(url, headers={"accept": "application/json"})
        response.raise_for_status()
        try:
            document = response.json()
        except ValueError as exc:
            raise ValueError("the response is not JSON") from exc
    missing = [key for key in _REQUIRED_METADATA if not document.get(key)]
    if missing:
        raise ValueError(f"discovery document is missing: {', '.join(missing)}")
    return document
