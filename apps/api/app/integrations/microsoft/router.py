"""REST endpoints for the microsoft core under ``/api/v1/microsoft`` (docs/MICROSOFT.md §3).

The connect/callback pair is browser-navigated (the session cookie rides along), so both
resolve the normal request context — unlike OIDC *login*, someone is already signed in here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx
from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.config import settings
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.microsoft.oauth import (
    SCOPE_MAIL,
    connect_client,
    microsoft_settings_row,
    normalise_scopes,
    safe_return_path,
    scopes_for,
)
from app.integrations.microsoft.schemas import (
    MicrosoftConnectionRead,
    MicrosoftSettingsRead,
    MicrosoftSettingsWrite,
    MyMicrosoftConnectionRead,
    MyMicrosoftConnectionUpdate,
)
from app.integrations.microsoft.service import (
    MicrosoftConnectionsService,
    MicrosoftSettingsService,
)

logger = logging.getLogger("schakl.microsoft")

router = APIRouter(prefix="/microsoft", tags=["microsoft"])

#: Where the browser lands after the connect round-trip when the caller named nowhere else.
_ACCOUNT_PAGE = "/settings/account"
_OUTLOOK_OPTIN_SESSION_KEY = "microsoft_connect_outlook"
#: Where to send the browser back to, stashed across the round-trip in the session: Microsoft
#: echoes neither the OAuth state's contents nor a redirect URI that varies per page.
_RETURN_SESSION_KEY = "microsoft_connect_next"


def _finish(request: Request, marker: str) -> RedirectResponse:
    """Land the browser back where the connect started, with the outcome in the query."""
    target = safe_return_path(request.session.pop(_RETURN_SESSION_KEY, ""), _ACCOUNT_PAGE)
    separator = "&" if "?" in target else "?"
    return RedirectResponse(url=f"{target}{separator}microsoft={marker}")


# --- org settings (Instellingen → Microsoft 365) --------------------------------- #
@router.get(
    "/settings",
    response_model=MicrosoftSettingsRead,
    dependencies=[require_permission("microsoft.settings.manage")],
)
async def get_settings(ctx: RequestContext = Depends(require_context)) -> MicrosoftSettingsRead:
    return await MicrosoftSettingsService(ctx).get()


@router.put(
    "/settings",
    response_model=MicrosoftSettingsRead,
    dependencies=[require_permission("microsoft.settings.manage")],
)
async def save_settings(
    payload: MicrosoftSettingsWrite,
    ctx: RequestContext = Depends(require_context),
) -> MicrosoftSettingsRead:
    return await MicrosoftSettingsService(ctx).save(payload)


# --- connections ---------------------------------------------------------------- #
@router.get(
    "/connections",
    response_model=list[MicrosoftConnectionRead],
    dependencies=[require_permission("microsoft.settings.manage")],
)
async def list_connections(
    ctx: RequestContext = Depends(require_context),
) -> list[MicrosoftConnectionRead]:
    return await MicrosoftConnectionsService(ctx).list()


@router.get(
    "/connections/me",
    response_model=MyMicrosoftConnectionRead,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def my_connection(
    ctx: RequestContext = Depends(require_context),
) -> MyMicrosoftConnectionRead:
    return await MicrosoftConnectionsService(ctx).me()


@router.patch(
    "/connections/me",
    response_model=MyMicrosoftConnectionRead,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def update_my_connection(
    payload: MyMicrosoftConnectionUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MyMicrosoftConnectionRead:
    return await MicrosoftConnectionsService(ctx).update_me(payload)


@router.post(
    "/connections/me/disconnect",
    status_code=204,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def disconnect_my_connection(ctx: RequestContext = Depends(require_context)) -> None:
    await MicrosoftConnectionsService(ctx).disconnect_me()


# --- the connect flow: a separate grant from login (docs/MICROSOFT.md §1) ---------- #
@router.get(
    "/oauth/connect",
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def oauth_connect(
    request: Request,
    include_outlook: bool = Query(False),
    next: str = Query("", alias="next"),  # noqa: A002 — the web's conventional name for it
    ctx: RequestContext = Depends(require_context),
):
    """302 to Microsoft's consent screen, asking exactly the enabled surfaces' scopes.

    ``offline_access`` rides every consent (it *is* the refresh token); ``prompt=select_account``
    lets someone signed into a personal account at Microsoft pick the work one this is about.
    ``next`` is where to land afterwards (site-relative only, :func:`safe_return_path`).
    """
    row = await microsoft_settings_row(ctx.session, ctx.org.id)
    client = connect_client(ctx.org.id, row)
    scopes = scopes_for(row, include_outlook=include_outlook)
    request.session[_OUTLOOK_OPTIN_SESSION_KEY] = "1" if include_outlook else ""
    request.session[_RETURN_SESSION_KEY] = safe_return_path(next, _ACCOUNT_PAGE)
    redirect_uri = str(request.url_for("microsoft_oauth_callback"))
    return await client.authorize_redirect(
        request,
        redirect_uri,
        scope=" ".join(scopes),
        prompt="select_account",
    )


@router.get(
    "/oauth/callback",
    name="microsoft_oauth_callback",
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def oauth_callback(
    request: Request,
    ctx: RequestContext = Depends(require_context),
):
    """Store the grant and land the browser back where the connect started.

    Identity comes from Graph's ``/me`` with the access token just issued (no id token — see
    :mod:`app.integrations.microsoft.oauth`). A denied consent, a state mismatch or a ``/me`` that
    will not answer is a redirect with an error marker, never a JSON envelope — a human is
    holding this request.
    """
    row = await microsoft_settings_row(ctx.session, ctx.org.id)
    client = connect_client(ctx.org.id, row)
    try:
        token = await client.authorize_access_token(request)
    except OAuthError:
        logger.warning("Microsoft connect callback failed", exc_info=True)
        return _finish(request, "error")

    access_token = token.get("access_token")
    if not access_token:
        return _finish(request, "error")
    try:
        async with ctx.release_db(), httpx.AsyncClient(timeout=15.0) as http:
            me = await http.get(
                f"{settings.microsoft_graph_base_url.rstrip('/')}/me",
                params={"$select": "id,mail,userPrincipalName,displayName"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            me.raise_for_status()
            profile = me.json()
    except httpx.HTTPError:
        logger.warning("Microsoft connect: /me refused the new token", exc_info=True)
        return _finish(request, "error")

    oid = profile.get("id")
    email = profile.get("mail") or profile.get("userPrincipalName")
    if not oid or not email:
        return _finish(request, "error")

    granted_scopes = normalise_scopes(token.get("scope"))
    expires_at = token.get("expires_at")
    connection = await MicrosoftConnectionsService(ctx).upsert_from_callback(
        user_id=ctx.user.id,
        microsoft_oid=str(oid),
        tenant_id=_tenant_of(token),
        email=str(email),
        granted_scopes=granted_scopes,
        refresh_token=token.get("refresh_token"),
        access_token=access_token,
        expires_at=(datetime.fromtimestamp(int(expires_at), tz=UTC) if expires_at else None),
    )
    # The Outlook opt-in only sticks when Microsoft actually granted the mail scope.
    if request.session.pop(_OUTLOOK_OPTIN_SESSION_KEY, "") and SCOPE_MAIL in granted_scopes:
        connection.outlook_sync_enabled = True
        await ctx.session.flush()
    return _finish(request, "connected")


def _tenant_of(token: dict) -> str | None:
    """The directory the account signed in from, when the token response names it. The v2.0
    endpoint does not put it on the access token response; an id token would, and we ask for
    none — so this is usually ``None`` and the registration's tenant setting is the fact."""
    value = token.get("tid") or token.get("tenant_id")
    return str(value)[:128] if value else None
