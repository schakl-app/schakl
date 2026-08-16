"""User manager (FastAPI Users) — verification & password-reset flows.

Password-reset (and invite, which rides the same token — #161) emails go through the
tenant-branded org transport (#17); a missing transport degrades to the P0 behaviour of
logging the token. Password hashing uses FastAPI Users' default (Argon2 via pwdlib).

**The account lookup is scoped to the request's org.** ``users`` is instance-level, so
``get_by_email`` answered from every tenant at once — which is the single lookup behind three
separate cross-tenant flaws on a multi-org instance: a member of org B could authenticate on
org A's hostname (``authenticate`` calls it), could have a password-reset mail sent to them
from org A's branded transport, and could be probed for existence from a tenant they have
nothing to do with. Narrowing this one method fixes all three, because the framework routes
that matter (``/auth/login``, ``/auth/forgot-password``, ``/auth/request-verify``) all reach
the account through it.

Two lookups deliberately stay **global**, and both are uniqueness checks rather than
authentication: ``create`` goes through ``self.user_db.get_by_email`` (registering an address
that exists in another tenant must still collide — ``users.email`` is globally unique), and
``POST /users/me/email`` runs its own global query (``account.py``). Scoping either would turn
a clean 409 into an integrity error.

A host that resolves to **no** org does not narrow anything: the cloud console lives on the
apex where no tenant exists (docs/CLOUD.md), and an instance owner must still be able to log
in there. That session names no org and so reaches no tenant data (``backend.py``).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi_users import BaseUserManager, InvalidPasswordException, UUIDIDMixin, exceptions
from sqlalchemy import select

from app.config import settings
from app.core.auth.models import User
from app.db import async_session_maker, set_current_org

logger = logging.getLogger("schakl.auth")

#: Mirror of the setup wizard's rule (``setup.py``) — one password policy, everywhere (#161).
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


async def member_of_request_org(request: Request | None, user: User) -> bool:
    """Does ``user`` hold a **live** membership in the org this request's hostname resolves to?

    ``True`` when there is no request to resolve against, and when the host resolves to no org
    (the console apex, and any pre-tenant caller) — this narrows a lookup, it does not invent a
    tenant. Its own session, like every pre-auth read: ``memberships`` is RLS-forced, so the GUC
    goes on before the membership read. The org itself comes from ``request_org_id``, which the
    login route resolves anyway, so the two share one hostname lookup.

    A **deactivated** membership answers exactly as a missing one does, and that is the point:
    this is the lookup behind login, password reset and request-verify alike, so a colleague who
    has left gets ``LOGIN_BAD_CREDENTIALS`` and a 202 — the same answers an address that was
    never here gets. Refusing later, or more loudly, would confirm the account still exists to
    somebody typing addresses at the login form. It is also the only place the *whole* sign-in
    surface can be closed at once: ``require_context`` refuses the session that would follow, but
    a check there alone would leave "the password was right" observable.
    """
    if request is None:
        return True
    # Imported here, not at module scope: ``tenancy`` imports the auth package (the same cycle
    # ``sso.require_local_login`` steps around).
    from app.core.models import Membership
    from app.core.tenancy import request_org_id

    org_id = await request_org_id(request)
    if org_id is None:
        return True
    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        found = await session.scalar(
            select(Membership.id).where(
                Membership.org_id == org_id,
                Membership.user_id == user.id,
                Membership.deactivated_at.is_(None),
            )
        )
    return found is not None


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    def __init__(self, user_db, request: Request | None = None) -> None:  # noqa: ANN001
        super().__init__(user_db)
        #: Resolved lazily, only by :meth:`get_by_email` — this manager is a dependency of
        #: *every* authenticated request (FastAPI Users' ``Authenticator``), so resolving the
        #: org up front would put an extra query on the whole app (docs/PERFORMANCE.md).
        self.request = request

    async def get_by_email(self, user_email: str) -> User:
        """The account with this address **in the request's org**, or ``UserNotExists``.

        Callers cannot tell "no such account" from "not one of ours", which is the point: the
        password route answers ``LOGIN_BAD_CREDENTIALS`` either way and the reset route answers
        202 either way, so neither confirms that an address exists in some other tenant.
        """
        user = await super().get_by_email(user_email)
        if not await member_of_request_org(self.request, user):
            logger.info("Account lookup refused: %s is not a member of this org", user_email)
            raise exceptions.UserNotExists()
        return user

    async def validate_password(self, password: str, user) -> None:  # noqa: ANN001 — FastAPI Users' contract
        """One policy for register, reset and update (#161) — FastAPI Users' default accepts
        any string. Reasons are i18n keys, surfaced by the web as-is."""
        if len(password) < PASSWORD_MIN_LENGTH:
            raise InvalidPasswordException(reason="errors.password_too_short")
        if len(password) > PASSWORD_MAX_LENGTH:
            raise InvalidPasswordException(reason="errors.password_too_long")
        email = (getattr(user, "email", "") or "").lower()
        if email and password.lower() == email:
            raise InvalidPasswordException(reason="errors.password_is_email")

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("User registered: %s", user.email)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        from app.core.auth.emails import send_password_email

        # An invite (#161) rides the same token; the caller marks the flavour on the request.
        kind = getattr(request.state, "password_email_kind", "reset") if request else "reset"
        sent, error = await send_password_email(
            self.user_db.session, user, token, request, kind=kind
        )
        # FastAPI Users' hook returns nothing, so the outcome rides the request state — the
        # invite endpoints read it to report honestly instead of assuming the mail went out.
        if request is not None:
            request.state.password_email_result = (sent, error)

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        """Setting a password through the emailed link proves the mailbox — that IS
        verification. Drives the portal's invited → active status (#193), and is equally
        true for a staff invite (#161), which rides the same token."""
        if not user.is_verified:
            await self.user_db.update(user, {"is_verified": True})

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        # Never log the raw verification token (audit F21): it is a bearer credential that
        # completes email verification for the account. Log only the event.
        logger.info("Verification requested for %s", user.email)
