"""``/api/v1/oauth`` — the endpoints a machine talks to, and the two a browser does.

**These routes answer in the RFC's error shape, not the house envelope**, and that is the one
place this module is allowed to disagree with §9. The caller is somebody else's MCP client
reading ``{"error": "invalid_grant"}`` off a documented contract; handing it
``{"error": {"code": …, "message": "errors.oauth_invalid_grant"}}`` would be an i18n key sent to
a program that cannot translate it, inside a field it will try to string-compare. The consent
endpoints — the two a browser calls — keep the house envelope, because the thing rendering their
errors *is* our web app.

Everything here runs before a credential exists, except consent, which runs on the session the
web app already holds. The org comes from the hostname either way (§5): a client registered on
one tenant's host is that tenant's, and is simply not found on another's.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.apikeys.models import ApiKey
from app.core.auth.ratelimit import limit_by_ip
from app.core.oauth.metadata import (
    authorization_server_document,
    protected_resource_document,
)
from app.core.oauth.models import OAuthClient
from app.core.oauth.service import (
    COARSE_SCOPES,
    OAuthService,
    expand_scopes,
    validate_redirect_uris,
)
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import (
    RequestContext,
    external_origin,
    request_hostname,
    require_context,
    resolve_org,
)
from app.db import async_session_maker, set_current_org
from app.errors import AppError

router = APIRouter(prefix="/oauth", tags=["oauth"])

#: Registration writes a row for an unauthenticated stranger — the only route here that does.
_REGISTER_LIMIT_PER_MINUTE = 10


def _oauth_error(code: str, description: str, status_code: int = 400) -> JSONResponse:
    """RFC 6749 §5.2. ``error`` is a machine token the client branches on; ``error_description``
    is prose for a developer's console and is deliberately not localised — nobody is reading it
    in Dutch inside somebody else's log."""
    return JSONResponse(
        {"error": code, "error_description": description},
        status_code=status_code,
        # A token response must never be cached, and neither must a refusal to issue one.
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


class _Tenant:
    """A hostname-resolved, RLS-bound session for the unauthenticated half of the flow.

    Written as a dependency rather than inline in five handlers because forgetting
    ``set_current_org`` on any one of them would be an *unscoped write* — Golden Rule 1 — and
    the failure would be a row nobody can read back rather than an error anyone would notice.
    """

    def __init__(self, session, org) -> None:  # noqa: ANN001
        self.session = session
        self.org = org
        self.service = OAuthService(session, org.id)


async def tenant_session(request: Request):  # noqa: ANN201
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("not_found", "errors.unknown_host", status_code=404)
        await set_current_org(session, org.id)
        yield _Tenant(session, org)


# --- discovery ----------------------------------------------------------------------------- #


@router.get(
    "/metadata/authorization-server",
    dependencies=[
        no_permission_required("RFC 8414 discovery; a client reads it before it has a credential")
    ],
)
async def authorization_server_metadata(request: Request) -> dict[str, Any]:
    """Served here and *proxied* by the web app at ``/.well-known/oauth-authorization-server``.

    The RFC puts it on the root of the host and the edge gives the root to the web app
    (docs/MCP.md), so this is the copy the proxy reads. Deliberately not duplicated in the web
    app: a second literal of the token endpoint's URL is a second thing to forget to change.
    """
    return authorization_server_document(external_origin(request))


@router.get(
    "/metadata/protected-resource",
    dependencies=[
        no_permission_required("RFC 9728 discovery; the 401 challenge on /mcp points at it")
    ],
)
async def protected_resource_metadata(
    request: Request,
    resource_path: Annotated[str, Query(pattern=r"^/mcp(/[A-Za-z0-9_-]+)?$")] = "/mcp",
) -> dict[str, Any]:
    """One document per ``/mcp`` URL, section segment included.

    The path is a *parameter* rather than a route so that a section added tomorrow is
    discoverable tomorrow — and it is pattern-bound rather than free text because it is echoed
    into the document as the resource identifier a token gets bound to.
    """
    return protected_resource_document(external_origin(request), resource_path)


# --- registration (RFC 7591) --------------------------------------------------------------- #


class ClientRegistration(BaseModel):
    """RFC 7591's request. Unknown members are ignored rather than refused — the registry of
    metadata names is open, and a client sending one we do not read is not an error."""

    client_name: str = Field(default="MCP client", max_length=200)
    redirect_uris: list[str] = Field(default_factory=list)
    client_uri: str | None = Field(default=None, max_length=1024)
    logo_uri: str | None = Field(default=None, max_length=1024)
    token_endpoint_auth_method: str = "none"

    model_config = {"extra": "ignore"}


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    dependencies=[
        no_permission_required(
            "RFC 7591 dynamic client registration: this is how a client that has never met "
            "this instance becomes addable at all, so it necessarily precedes every credential"
        )
    ],
)
async def register_client(
    request: Request,
    body: ClientRegistration,
    tenant: _Tenant = Depends(tenant_session),
) -> Response:
    """Register a client. Registering grants **nothing** — it names a thing a person may later
    consent to, and until somebody does, the row can read no byte of tenant data.

    Rate-limited by IP, because it is the one unauthenticated write in the codebase that a
    stranger can repeat.
    """
    await limit_by_ip(request, bucket="oauth_register", limit=_REGISTER_LIMIT_PER_MINUTE)
    try:
        client, secret = await tenant.service.register_client(
            client_name=body.client_name,
            redirect_uris=body.redirect_uris,
            client_uri=body.client_uri,
            logo_uri=body.logo_uri,
            confidential=body.token_endpoint_auth_method == "client_secret_post",
        )
    except AppError as exc:
        return _oauth_error("invalid_client_metadata", exc.message_key, status_code=exc.status_code)
    await tenant.session.commit()
    payload: dict[str, Any] = {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": client.redirect_uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post" if secret else "none",
    }
    if secret:
        payload["client_secret"] = secret
    return JSONResponse(payload, status_code=201, headers={"Cache-Control": "no-store"})


# --- consent (the browser half) ------------------------------------------------------------ #


class ConsentScope(BaseModel):
    value: str
    label_key: str
    read: bool


class ConsentRequest(BaseModel):
    """What the consent screen renders. Everything here is either the client's own words or the
    catalog's — no tenant data, because the person reading it has not agreed to anything yet."""

    client_name: str
    client_uri: str | None
    redirect_uri: str
    scopes: list[ConsentScope]
    resource: str | None


@router.get(
    "/consent",
    response_model=ConsentRequest,
    dependencies=[
        no_permission_required(
            "the consent screen describes the caller's own account and the client asking for "
            "it; approving is what needs a permission, and POST /oauth/consent declares one"
        )
    ],
)
async def consent_request(
    client_id: str,
    redirect_uri: str,
    scope: str = "",
    resource: str | None = None,
    ctx: RequestContext = Depends(require_context),
) -> ConsentRequest:
    """Validate an authorization request and describe it, without writing anything.

    A person who opens the screen and closes it again leaves no row behind — which is why the
    grant is written at approval and not here.
    """
    service = OAuthService(ctx.session, ctx.org.id)
    client = await service.require_client(client_id, redirect_uri)
    requested = [s for s in scope.split() if s]
    resolved = expand_scopes(requested, ctx.permissions)
    from app.core.permissions.catalog import all_permissions

    labels = {spec.key: spec.label_key for spec in all_permissions()}
    return ConsentRequest(
        client_name=client.client_name,
        client_uri=client.client_uri,
        redirect_uri=redirect_uri,
        scopes=[
            ConsentScope(
                value=value,
                label_key=labels.get(value.split(":")[0], value),
                read=value.split(":")[0].rsplit(".", 1)[-1] == "read",
            )
            for value in resolved
        ],
        resource=resource,
    )


class ConsentApproval(BaseModel):
    client_id: str
    redirect_uri: str
    code_challenge: str
    code_challenge_method: str = "S256"
    scopes: list[str] = Field(default_factory=list)
    resource: str | None = None
    state: str | None = None


class ConsentResult(BaseModel):
    redirect_to: str


@router.post(
    "/consent",
    response_model=ConsentResult,
    dependencies=[require_permission("apikeys.personal.manage")],
)
async def approve_consent(
    body: ConsentApproval, ctx: RequestContext = Depends(require_context)
) -> ConsentResult:
    """Approve, and get the URL to send the browser back to.

    Gated on ``apikeys.personal.manage`` — the same permission the key screen requires — because
    that is exactly what this is: minting a personal key, with a redirect instead of a copy
    button. A member who may not mint one by hand may not mint one by consenting either.
    """
    service = OAuthService(ctx.session, ctx.org.id)
    client = await service.require_client(body.client_id, body.redirect_uri)
    redirect_to = await service.approve(
        client=client,
        ctx=ctx,
        redirect_uri=body.redirect_uri,
        code_challenge=body.code_challenge,
        code_challenge_method=body.code_challenge_method,
        scopes=body.scopes,
        resource=body.resource,
        state=body.state,
    )
    return ConsentResult(redirect_to=redirect_to)


# --- token (the machine half) -------------------------------------------------------------- #


@router.post(
    "/token",
    dependencies=[
        no_permission_required(
            "RFC 6749 token exchange: the code and the PKCE verifier *are* the credential, and "
            "the point of the call is that the caller does not have one yet"
        )
    ],
)
async def token(
    grant_type: Annotated[str, Form()],
    tenant: _Tenant = Depends(tenant_session),
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
    refresh_token: Annotated[str | None, Form()] = None,
    client_id: Annotated[str | None, Form()] = None,
    client_secret: Annotated[str | None, Form()] = None,
    resource: Annotated[str | None, Form()] = None,
) -> Response:
    """``authorization_code`` and ``refresh_token``. Form-encoded, per the RFC.

    What comes back is an ``api_keys`` secret — a real key, usable at ``/mcp`` and at the REST
    API alike, and capped by the consenting user's live permissions on every request.
    """
    if not client_id:
        return _oauth_error("invalid_client", "client_id is required")
    client = await tenant.service.client_by_id(client_id)
    if client is None:
        return _oauth_error("invalid_client", "unknown client_id", status_code=401)
    if client.secret_hash is not None:
        from app.core.apikeys import keys as keygen

        if not client_secret or not keygen.verify_secret(client_secret, client.secret_hash):
            return _oauth_error("invalid_client", "client authentication failed", status_code=401)

    try:
        if grant_type == "authorization_code":
            if not (code and redirect_uri and code_verifier):
                return _oauth_error(
                    "invalid_request", "code, redirect_uri and code_verifier are required"
                )
            access, refresh, expires_in, scopes = await tenant.service.redeem_code(
                client=client, code=code, redirect_uri=redirect_uri, code_verifier=code_verifier
            )
        elif grant_type == "refresh_token":
            if not refresh_token:
                return _oauth_error("invalid_request", "refresh_token is required")
            access, refresh, expires_in, scopes = await tenant.service.refresh(
                client=client, refresh_token=refresh_token
            )
        else:
            return _oauth_error("unsupported_grant_type", f"unsupported grant_type {grant_type!r}")
    except AppError as exc:
        code_name = "invalid_grant" if exc.code == "invalid_grant" else "invalid_request"
        return _oauth_error(code_name, exc.message_key, status_code=exc.status_code)

    await tenant.session.commit()
    return JSONResponse(
        {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "refresh_token": refresh,
            "scope": " ".join(scopes),
        },
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@router.post(
    "/revoke",
    status_code=status.HTTP_200_OK,
    dependencies=[
        no_permission_required(
            "RFC 7009: the token being revoked is the only credential the call needs, and a "
            "client hanging up should not have to hold a second one to do it politely"
        )
    ],
)
async def revoke(
    token: Annotated[str, Form()],
    tenant: _Tenant = Depends(tenant_session),
) -> Response:
    """Always 200. A revocation endpoint that distinguishes "revoked" from "no such token" is a
    token oracle, and the caller can do nothing with the difference anyway (RFC 7009 §2.2)."""
    await tenant.service.revoke_token(token)
    await tenant.session.commit()
    return JSONResponse({}, headers={"Cache-Control": "no-store"})


# --- what the settings screen shows -------------------------------------------------------- #


class ConnectionRead(BaseModel):
    """One connected client, as Instellingen → API en MCP lists it."""

    id: uuid.UUID
    client_name: str
    client_uri: str | None
    created_at: Any
    last_used_at: Any
    #: Live sessions this client holds for the *calling* user. A client is per-tenant; a session
    #: is per-person, and nobody is shown somebody else's.
    sessions: int


@router.get(
    "/connections",
    response_model=list[ConnectionRead],
    dependencies=[require_permission("apikeys.personal.manage")],
)
async def list_connections(ctx: RequestContext = Depends(require_context)) -> list[ConnectionRead]:
    """The clients this user has connected. Counted in one grouped read, not one query per row."""
    rows = (
        (
            await ctx.session.execute(
                select(OAuthClient, ApiKey.id)
                .outerjoin(
                    ApiKey,
                    (ApiKey.oauth_client_id == OAuthClient.id)
                    & (ApiKey.user_id == ctx.user.id)
                    & (ApiKey.revoked_at.is_(None)),
                )
                .where(OAuthClient.revoked_at.is_(None))
                .order_by(OAuthClient.created_at.desc())
            )
        )
        .unique()
        .all()
    )
    counted: dict[uuid.UUID, ConnectionRead] = {}
    for client, key_id in rows:
        entry = counted.get(client.id)
        if entry is None:
            entry = counted[client.id] = ConnectionRead(
                id=client.id,
                client_name=client.client_name,
                client_uri=client.client_uri,
                created_at=client.created_at,
                last_used_at=client.last_used_at,
                sessions=0,
            )
        if key_id is not None:
            entry.sessions += 1
    return list(counted.values())


@router.delete(
    "/connections/{client_pk}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[require_permission("apikeys.personal.manage")],
)
async def disconnect(
    client_pk: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> Response:
    """Disconnect a client: revoke it, and every key it ever issued goes with it.

    Revoking the *client* rather than this user's keys is the honest kill switch — a connector
    that has been disconnected must not be able to refresh its way back in, and a refresh
    presented against a revoked client is refused before any key is looked at.
    """
    client = await ctx.session.get(OAuthClient, client_pk)
    if client is None or client.org_id != ctx.org.id:
        raise AppError("not_found", "errors.not_found", status_code=404)
    from datetime import UTC, datetime

    client.revoked_at = datetime.now(UTC)
    for key in (
        (await ctx.session.execute(select(ApiKey).where(ApiKey.oauth_client_id == client.id)))
        .scalars()
        .all()
    ):
        key.revoked_at = key.revoked_at or datetime.now(UTC)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["COARSE_SCOPES", "router", "validate_redirect_uris"]
