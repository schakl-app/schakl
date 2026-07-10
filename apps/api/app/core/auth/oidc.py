"""Optional OIDC relying-party via Authlib (CLAUDE.md §3).

Disabled by default. When ``OIDC_ENABLED`` (and configured), this contributes login/callback
routes that federate to an external IdP and issue the platform's own session cookie. When
``OIDC_ENFORCED``, local username/password login is turned off (see ``router.py``).

Not exercised by the P0 gate (needs a real IdP); written defensively so import/mount never
fails when unconfigured.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.core.auth.backend import cookie_transport, get_jwt_strategy
from app.core.auth.models import User
from app.db import async_session_maker

logger = logging.getLogger("schakl.auth.oidc")


def build_oidc_router() -> APIRouter | None:
    if not settings.oidc_configured:
        missing = settings.oidc_missing_settings
        if settings.oidc_enforced:
            # Enforced OIDC disables local login; silently dropping the SSO routes on top
            # of that would lock every user out. Refuse to boot instead (issue #6).
            raise RuntimeError(
                "SCHAKL_OIDC_ENFORCED=true but OIDC is not configured, which would disable "
                f"local login with no SSO to fall back on. Set: {', '.join(missing)}."
            )
        if settings.oidc_enabled:
            logger.warning(
                "OIDC is enabled but not configured; the SSO routes are not mounted and "
                "the login page will not offer SSO. Set: %s.",
                ", ".join(missing),
            )
        return None

    from authlib.integrations.starlette_client import OAuth

    oauth = OAuth()
    oauth.register(
        name=settings.oidc_name,
        server_metadata_url=settings.oidc_discovery_url,
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret,
        client_kwargs={"scope": "openid email profile"},
    )
    client = getattr(oauth, settings.oidc_name)
    router = APIRouter()

    @router.get("/login")
    async def oidc_login(request: Request):
        redirect_uri = str(request.url_for("oidc_callback"))
        return await client.authorize_redirect(request, redirect_uri)

    @router.get("/callback", name="oidc_callback")
    async def oidc_callback(request: Request):
        token = await client.authorize_access_token(request)
        userinfo = token.get("userinfo") or await client.userinfo(token=token)
        email = userinfo.get("email")
        if not email:
            return RedirectResponse(url="/login?error=oidc")

        async with async_session_maker() as session:
            from sqlalchemy import select

            from app.core.models import Membership
            from app.core.permissions.service import create_membership
            from app.core.tenancy import request_hostname, resolve_org
            from app.db import set_current_org

            user = await session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    id=uuid.uuid4(),
                    email=email,
                    full_name=userinfo.get("name"),
                    # Unusable local password; identity is asserted by the IdP.
                    hashed_password=uuid.uuid4().hex,
                    is_active=True,
                    is_verified=True,
                )
                session.add(user)
                await session.flush()

            # Grant a membership in the resolved org, otherwise a JIT-provisioned SSO user would
            # authenticate but be locked out (no membership → 403 in require_context).
            if settings.oidc_auto_provision_membership:
                org = await resolve_org(session, request_hostname(request))
                if org is not None:
                    await set_current_org(session, org.id)
                    existing = await session.scalar(
                        select(Membership).where(
                            Membership.org_id == org.id, Membership.user_id == user.id
                        )
                    )
                    if existing is None:
                        # Goes through the RBAC helper so a JIT-provisioned user also holds the
                        # system role that carries their permissions (issue #19) — a membership
                        # without one authenticates and can then do nothing at all.
                        await create_membership(
                            session, org.id, user.id, settings.oidc_default_role
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

    return router
