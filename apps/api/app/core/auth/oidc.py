"""OIDC relying-party via Authlib — per-org, resolved at request time (issue #76).

The ``/auth/oidc/login`` and ``/callback`` routes are mounted **unconditionally**. Each request
resolves the org from the hostname (the same strict resolution every request uses, CLAUDE.md §5),
loads that org's stored SSO settings (:mod:`app.core.auth.sso`) and builds/reuses the Authlib
client for exactly that config. An org that has SSO disabled or half-configured gets a clean
``404 errors.sso_not_configured`` — the same status an unmounted route used to give, so the
login button's invariant (shown iff the flow works) survives the move to per-org config.

Enabling, disabling or enforcing SSO is a settings write; nothing here is decided at boot.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.core.auth import sso
from app.core.auth.backend import cookie_transport, get_jwt_strategy
from app.core.auth.models import User
from app.db import async_session_maker, set_current_org
from app.errors import AppError

logger = logging.getLogger("schakl.auth.oidc")

router = APIRouter()


@dataclass
class _ResolvedSso:
    """The per-request view of one org's SSO config — plain values, no live session."""

    org_id: uuid.UUID
    client: object
    auto_provision: bool
    default_role: str


async def _resolve_sso(request: Request) -> _ResolvedSso:
    """Hostname → org → stored config → Authlib client, or a clean error.

    The org resolves before any user exists (exactly like the login screen's branding read):
    resolve first, bind the RLS GUC, then read the RLS-forced settings row.
    """
    from app.core.tenancy import request_hostname, resolve_org

    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("unknown_host", "errors.unknown_host", status_code=404)
        await set_current_org(session, org.id)
        row = await sso.sso_row(session, org.id)
        if not sso.sso_configured(row):
            raise AppError(
                "sso_not_configured", "errors.sso_not_configured", status_code=404
            )
        assert row is not None  # sso_configured guarantees it; keeps the type-checker honest
        return _ResolvedSso(
            org_id=org.id,
            client=sso.oauth_client(row),
            auto_provision=row.oidc_auto_provision_membership,
            default_role=row.oidc_default_role,
        )


@router.get("/login")
async def oidc_login(request: Request):
    resolved = await _resolve_sso(request)
    redirect_uri = str(request.url_for("oidc_callback"))
    return await resolved.client.authorize_redirect(request, redirect_uri)


async def _claims(client: Any, token: dict) -> dict[str, Any]:
    """The caller's OIDC claims, with the userinfo endpoint filling what the id_token omits.

    ``authorize_access_token`` parses the validated id_token into ``token["userinfo"]`` for every
    IdP that returns one — which is every OIDC login — so reading ``token["userinfo"]`` alone
    never consults the userinfo endpoint. But the id_token is an *authentication* token: several
    IdPs, **Google among them for many accounts**, leave the ``picture`` profile claim (and
    sometimes ``name``) out of it and expose it only at the userinfo endpoint. That is exactly
    why avatars were not being imported (#122): the picture lived one HTTP call away and the code
    never made it.

    So fetch both and merge — the id_token stays authoritative for identity, the endpoint fills
    the profile gaps. Enrichment is best-effort: a userinfo failure (unreachable endpoint, an IdP
    that has none) must never break a login the id_token already authenticated, so it is logged
    and swallowed, leaving the id_token claims to stand.
    """
    id_claims = dict(token.get("userinfo") or {})
    endpoint_claims: dict[str, Any] = {}
    try:
        endpoint_claims = dict(await client.userinfo(token=token))
    except Exception:  # noqa: BLE001 — profile enrichment is best-effort; identity is not
        logger.warning("OIDC userinfo fetch failed; using id_token claims only", exc_info=True)
    # id_claims last: the validated id_token wins on any conflict; the endpoint only fills gaps.
    return {**endpoint_claims, **id_claims}


def _email_verified(claims: dict[str, Any]) -> bool:
    """Whether the IdP vouches the email is verified. OIDC allows the claim as a JSON boolean or,
    from some providers, the string ``"true"``. Absent/false ⇒ not verified (fail closed)."""
    value = claims.get("email_verified")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes"}
    return False


@router.get("/callback", name="oidc_callback")
async def oidc_callback(request: Request):
    resolved = await _resolve_sso(request)
    token = await resolved.client.authorize_access_token(request)
    userinfo = await _claims(resolved.client, token)
    email = userinfo.get("email")
    if not email:
        return RedirectResponse(url="/login?error=oidc")

    async with async_session_maker() as session:
        from sqlalchemy import select

        from app.core.models import Membership
        from app.core.permissions.service import create_membership

        user = await session.scalar(select(User).where(User.email == email))
        if user is not None and not _email_verified(userinfo):
            # Account-takeover guard (audit C2): adopting a *pre-existing* local account by a bare,
            # IdP-asserted email is how an attacker on a permissive IdP (self-service signup, a
            # social connection) captures someone else's account — including the local /setup
            # superuser owner. Only link to an existing account when the IdP vouches the email is
            # verified. A brand-new JIT identity (user is None) is unaffected.
            logger.warning(
                "OIDC login refused: unverified email claim for existing account %s", email
            )
            return RedirectResponse(url="/login?error=oidc")
        if user is None:
            user = User(
                id=uuid.uuid4(),
                email=email,
                full_name=userinfo.get("name"),
                # The IdP's picture claim (#122) — `profile` scope already requested.
                oidc_avatar_url=userinfo.get("picture"),
                # Unusable local password; identity is asserted by the IdP.
                hashed_password=uuid.uuid4().hex,
                is_active=True,
                is_verified=True,
            )
            session.add(user)
            await session.flush()
        else:
            # Returning user: refresh the IdP-owned picture each login (#122) — a stale or
            # dropped claim follows through, while the personal override stays untouched.
            user.oidc_avatar_url = userinfo.get("picture")

        # Grant a membership in the resolved org, otherwise a JIT-provisioned SSO user would
        # authenticate but be locked out (no membership → 403 in require_context). Per-org
        # policy now: the org's stored auto-provision flag and default role (issue #76).
        if resolved.auto_provision:
            await set_current_org(session, resolved.org_id)
            existing = await session.scalar(
                select(Membership).where(
                    Membership.org_id == resolved.org_id, Membership.user_id == user.id
                )
            )
            if existing is None:
                # Goes through the RBAC helper so a JIT-provisioned user also holds the
                # system role that carries their permissions (issue #19) — a membership
                # without one authenticates and can then do nothing at all.
                await create_membership(
                    session, resolved.org_id, user.id, resolved.default_role
                )
        await session.commit()

    jwt = await get_jwt_strategy().write_token(user)
    response = RedirectResponse(url="/")
    response.set_cookie(
        key=cookie_transport.cookie_name,
        value=jwt,
        max_age=settings.auth_token_lifetime_seconds,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
    )
    return response
