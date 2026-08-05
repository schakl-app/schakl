"""Authentication backend: cookie transport + JWT strategy (CLAUDE.md §3).

The SSR web app authenticates via an httpOnly cookie, so tokens never touch client JS.

**A session belongs to exactly one org.** The account table is instance-level (``users`` is in
``INSTANCE_LEVEL_TABLES``) and the password check knows nothing about tenants, so without a
tenant binding *somewhere* the login route on org A's hostname happily minted a valid session
for a member of org B — the credentials were right, they were just right for a different
tenant. On a single-org self-hosted box that is invisible; on a multi-org instance (cloud, or
any box with a second org) it is a cross-tenant authentication boundary that does not exist.
``require_context`` would still refuse the *data*, but the session itself is a credential: it
was mintable, it was a real session on that hostname, and every future widening of what a
session alone can reach would have inherited the hole.

So the token carries the org it was minted for, in an ``org`` claim, and the two ends agree:

* **Minting** — every site that mints a session (``twofactor_router.login``, the 2FA challenge
  redemption, the OIDC callback, the impersonation handoff) resolves the request's org and
  passes it here. A host that resolves to **no** org — the cloud console's apex, where no
  tenant exists (docs/CLOUD.md) — mints an org-less token on purpose: it is an instance
  session, it authenticates the instance surface, and by the rule below it reaches no tenant
  data at all.
* **Presenting** — :func:`session_org` hands the claim to ``require_context``, which compares
  it against the org the hostname resolved to and refuses on any mismatch. Reading the claim
  costs nothing: the token is decoded once, on the request's own strategy, and the answer is
  parked on ``request.state`` for the dependency that already knows the org (docs/PERFORMANCE.md
  — an org lookup here would double the one ``require_context`` performs on every request).

A missing claim therefore fails closed: an org-less session reaches no tenant, and a token
issued by a release before this one is not a session for any org either. Both re-authenticate.
"""

from __future__ import annotations

import uuid

import jwt
from fastapi import Request, Response
from fastapi_users import exceptions
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.jwt import decode_jwt, generate_jwt
from fastapi_users.manager import BaseUserManager

from app.config import settings
from app.core.auth.models import User

cookie_transport = CookieTransport(
    cookie_name=settings.auth_cookie_name,
    cookie_max_age=settings.auth_token_lifetime_seconds,
    cookie_secure=settings.auth_cookie_secure,
    cookie_httponly=True,
    cookie_samesite="lax",
)

#: The JWT claim naming the org a session was minted for, and where the decoded value is parked
#: for the rest of the request. Absent claim ⇒ not a session for any tenant.
ORG_CLAIM = "org"
_STATE_ATTR = "schakl_session_org_id"


class OrgScopedJWTStrategy(JWTStrategy):
    """FastAPI Users' JWT strategy, plus the ``org`` claim and where to put it.

    ``org_id`` is the org a token being **written** belongs to (``None`` = an instance session,
    minted on a host that resolves to no org). ``request`` is where a token being **read** parks
    its claim, for ``require_context`` to enforce against the org it resolves anyway.
    """

    def __init__(
        self,
        *,
        org_id: uuid.UUID | None = None,
        request: Request | None = None,
        lifetime_seconds: int | None = None,
    ) -> None:
        super().__init__(
            secret=settings.secret_key,
            lifetime_seconds=(
                settings.auth_token_lifetime_seconds
                if lifetime_seconds is None
                else lifetime_seconds
            ),
        )
        self.org_id = org_id
        self.request = request

    async def write_token(self, user: User) -> str:  # type: ignore[override]
        data: dict[str, object] = {"sub": str(user.id), "aud": self.token_audience}
        if self.org_id is not None:
            data[ORG_CLAIM] = str(self.org_id)
        return generate_jwt(
            data, self.encode_key, self.lifetime_seconds, algorithm=self.algorithm
        )

    async def read_token(  # type: ignore[override]
        self, token: str | None, user_manager: BaseUserManager
    ) -> User | None:
        """The framework's own ``read_token``, with the ``org`` claim parked on the request.

        Deliberately a re-implementation rather than a ``super()`` call plus a second decode:
        the claim has to come out of the *same* verification that authenticated the token, or
        the two could disagree. The body below is FastAPI Users' verbatim — keep it that way if
        the pin moves.
        """
        if token is None:
            return None

        try:
            data = decode_jwt(
                token, self.decode_key, self.token_audience, algorithms=[self.algorithm]
            )
            user_id = data.get("sub")
            if user_id is None:
                return None
        except jwt.PyJWTError:
            return None

        if self.request is not None:
            self.request.state.__setattr__(_STATE_ATTR, _parse_org(data.get(ORG_CLAIM)))

        try:
            parsed_id = user_manager.parse_id(user_id)
            return await user_manager.get(parsed_id)
        except (exceptions.UserNotExists, exceptions.InvalidID):
            return None


def _parse_org(raw: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(raw))
    except (TypeError, ValueError):
        return None


def get_jwt_strategy(request: Request) -> OrgScopedJWTStrategy:
    """The per-request strategy. ``request`` is injected by FastAPI (``Depends(get_strategy)``)
    and is what gives :func:`session_org` something to read."""
    return OrgScopedJWTStrategy(request=request)


auth_backend = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)


def session_org(request: Request) -> uuid.UUID | None:
    """The org this request's **session cookie** was minted for, or ``None``.

    ``None`` covers every way a request is not a tenant session: no cookie at all, an instance
    session from the console's apex, and a token from a release that predates the claim. The
    caller (``require_context``) treats all three the same way — fail closed.
    """
    return getattr(request.state, _STATE_ATTR, None)


async def write_session_token(
    user: User, org_id: uuid.UUID | None, *, lifetime_seconds: int | None = None
) -> str:
    """A session token for ``user`` in ``org_id``. The one way a session is minted."""
    return await OrgScopedJWTStrategy(
        org_id=org_id, lifetime_seconds=lifetime_seconds
    ).write_token(user)


async def session_response(user: User, org_id: uuid.UUID | None) -> Response:
    """The login response — the httpOnly cookie carrying a session minted for ``org_id``.

    Replaces ``auth_backend.login(strategy, user)`` at every mint site: the framework's helper
    takes the strategy from the request's own dependency, which cannot know the org the route
    just resolved.
    """
    token = await write_session_token(user, org_id)
    return await cookie_transport.get_login_response(token)


async def issue_session_token(
    user: User, lifetime_seconds: int, org_id: uuid.UUID | None
) -> str:
    """A session token for ``user`` that expires in ``lifetime_seconds``.

    Same secret, same audience and therefore the same validation path as a token from the login
    route — only the lifetime differs. The one caller is the cross-host impersonation handoff
    (#288), which has to put the *administrator's* session on the tenant's hostname (cookies are
    host-scoped, so the console's own session is not there) and wants it to lapse exactly with
    the impersonation grant rather than to sit there for a week. ``org_id`` is the org being
    entered: the administrator's console session belongs to no tenant, so a token minted for
    this crossing has to name the one it is crossing into or ``require_context`` refuses it.
    """
    return await write_session_token(user, org_id, lifetime_seconds=lifetime_seconds)
