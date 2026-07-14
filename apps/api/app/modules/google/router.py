"""REST endpoints for the google core under ``/api/v1/google`` (docs/GOOGLE.md §3).

The connect/callback pair is browser-navigated (the session cookie rides along), so both
resolve the normal request context — unlike OIDC *login*, someone is already signed in here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from authlib.integrations.base_client.errors import OAuthError
from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import RedirectResponse

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.google.oauth import (
    SCOPE_GMAIL,
    connect_client,
    google_settings_row,
    scopes_for,
)
from app.modules.google.schemas import (
    ConnectionRead,
    GoogleSettingsRead,
    GoogleSettingsWrite,
    MyConnectionRead,
    MyConnectionUpdate,
)
from app.modules.google.service import GoogleConnectionsService, GoogleSettingsService

logger = logging.getLogger("schakl.google")

router = APIRouter(prefix="/google", tags=["google"])

#: Where the browser lands after the connect round-trip — the personal account page's card.
_ACCOUNT_PAGE = "/settings/account"
_GMAIL_OPTIN_SESSION_KEY = "google_connect_gmail"


# --- org settings (Instellingen → Google) ------------------------------------------ #
@router.get(
    "/settings",
    response_model=GoogleSettingsRead,
    dependencies=[require_permission("google.settings.manage")],
)
async def get_settings(ctx: RequestContext = Depends(require_context)) -> GoogleSettingsRead:
    return await GoogleSettingsService(ctx).get()


@router.put(
    "/settings",
    response_model=GoogleSettingsRead,
    dependencies=[require_permission("google.settings.manage")],
)
async def save_settings(
    payload: GoogleSettingsWrite,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSettingsRead:
    return await GoogleSettingsService(ctx).save(payload)


# --- connections ---------------------------------------------------------------- #
@router.get(
    "/connections",
    response_model=list[ConnectionRead],
    dependencies=[require_permission("google.settings.manage")],
)
async def list_connections(
    ctx: RequestContext = Depends(require_context),
) -> list[ConnectionRead]:
    return await GoogleConnectionsService(ctx).list()


@router.get(
    "/connections/me",
    response_model=MyConnectionRead,
    dependencies=[require_permission("google.connection.manage")],
)
async def my_connection(ctx: RequestContext = Depends(require_context)) -> MyConnectionRead:
    return await GoogleConnectionsService(ctx).me()


@router.patch(
    "/connections/me",
    response_model=MyConnectionRead,
    dependencies=[require_permission("google.connection.manage")],
)
async def update_my_connection(
    payload: MyConnectionUpdate,
    ctx: RequestContext = Depends(require_context),
) -> MyConnectionRead:
    return await GoogleConnectionsService(ctx).update_me(payload)


@router.post(
    "/connections/me/disconnect",
    status_code=204,
    dependencies=[require_permission("google.connection.manage")],
)
async def disconnect_my_connection(ctx: RequestContext = Depends(require_context)) -> None:
    await GoogleConnectionsService(ctx).disconnect_me()


# --- the connect flow: a separate grant from login (docs/GOOGLE.md §1) ------------- #
@router.get(
    "/oauth/connect",
    dependencies=[require_permission("google.connection.manage")],
)
async def oauth_connect(
    request: Request,
    include_gmail: bool = Query(False),
    include_analytics: bool = Query(False),
    include_search_console: bool = Query(False),
    include_ads: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
):
    """302 to Google's consent screen, asking exactly the enabled surfaces' scopes.

    ``access_type=offline`` + ``prompt=consent`` guarantee a refresh token on every connect;
    ``include_granted_scopes`` makes a later reconnect *add* scopes instead of replacing them
    (incremental authorization — the docs/GOOGLE.md §1 bridge). The ``include_analytics`` /
    ``include_search_console`` / ``include_ads`` flags are how the marketing module (epic #134)
    walks a connection up to its GA4/GSC/Ads scopes over this same flow — no second OAuth.
    """
    row = await google_settings_row(ctx.session, ctx.org.id)
    client = connect_client(ctx.org.id, row)
    scopes = scopes_for(
        row,
        include_gmail=include_gmail,
        include_analytics=include_analytics,
        include_search_console=include_search_console,
        include_ads=include_ads,
    )
    # Whether the user opted their mailbox in — read back on the callback leg.
    request.session[_GMAIL_OPTIN_SESSION_KEY] = "1" if include_gmail else ""
    redirect_uri = str(request.url_for("google_oauth_callback"))
    return await client.authorize_redirect(
        request,
        redirect_uri,
        scope=" ".join(scopes),
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )


@router.get(
    "/oauth/callback",
    name="google_oauth_callback",
    dependencies=[require_permission("google.connection.manage")],
)
async def oauth_callback(
    request: Request,
    ctx: RequestContext = Depends(require_context),
):
    """Store the grant and land the browser back on the account card.

    A denied consent or a state mismatch is a redirect with an error marker, never a JSON
    envelope — a human is holding this request.
    """
    row = await google_settings_row(ctx.session, ctx.org.id)
    client = connect_client(ctx.org.id, row)
    try:
        token = await client.authorize_access_token(request)
    except OAuthError:
        logger.warning("Google connect callback failed", exc_info=True)
        return RedirectResponse(url=f"{_ACCOUNT_PAGE}?google=error")

    userinfo = dict(token.get("userinfo") or {})
    sub, email = userinfo.get("sub"), userinfo.get("email")
    if not sub or not email:
        return RedirectResponse(url=f"{_ACCOUNT_PAGE}?google=error")

    granted_scopes = [s for s in str(token.get("scope") or "").split() if s]
    expires_at = token.get("expires_at")
    connection = await GoogleConnectionsService(ctx).upsert_from_callback(
        user_id=ctx.user.id,
        google_sub=str(sub),
        email=str(email),
        granted_scopes=granted_scopes,
        refresh_token=token.get("refresh_token"),
        access_token=token.get("access_token"),
        expires_at=(
            datetime.fromtimestamp(int(expires_at), tz=UTC) if expires_at else None
        ),
    )
    # The Gmail opt-in only sticks when Google actually granted the scope.
    if request.session.pop(_GMAIL_OPTIN_SESSION_KEY, "") and SCOPE_GMAIL in granted_scopes:
        connection.gmail_sync_enabled = True
        await ctx.session.flush()
    return RedirectResponse(url=f"{_ACCOUNT_PAGE}?google=connected")
