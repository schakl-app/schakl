"""User manager (FastAPI Users) — verification & password-reset flows.

Password-reset (and invite, which rides the same token — #161) emails go through the
tenant-branded org transport (#17); a missing transport degrades to the P0 behaviour of
logging the token. Password hashing uses FastAPI Users' default (Argon2 via pwdlib).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi_users import BaseUserManager, InvalidPasswordException, UUIDIDMixin

from app.config import settings
from app.core.auth.models import User

logger = logging.getLogger("schakl.auth")

#: Mirror of the setup wizard's rule (``setup.py``) — one password policy, everywhere (#161).
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 128


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

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
        await send_password_email(self.user_db.session, user, token, request, kind=kind)

    async def on_after_reset_password(self, user: User, request: Request | None = None) -> None:
        """Setting a password through the emailed link proves the mailbox — that IS
        verification. Drives the portal's invited → active status (#193), and is equally
        true for a staff invite (#161), which rides the same token."""
        if not user.is_verified:
            await self.user_db.update(user, {"is_verified": True})

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        logger.info("Verification requested for %s (token=%s)", user.email, token)
