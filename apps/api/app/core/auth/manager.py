"""User manager (FastAPI Users) — verification & password-reset flows.

P0 has no SMTP, so token-bearing events are logged (dev-visible) instead of emailed; wiring
the mailer is a later phase. Password hashing uses FastAPI Users' default (Argon2 via pwdlib).
"""

from __future__ import annotations

import logging
import uuid

from fastapi import Request
from fastapi_users import BaseUserManager, UUIDIDMixin

from app.config import settings
from app.core.auth.models import User

logger = logging.getLogger("vlotr.auth")


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    reset_password_token_secret = settings.secret_key
    verification_token_secret = settings.secret_key

    async def on_after_register(self, user: User, request: Request | None = None) -> None:
        logger.info("User registered: %s", user.email)

    async def on_after_forgot_password(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        # TODO(P1+): send via the tenant-branded mailer instead of logging.
        logger.info("Password reset requested for %s (token=%s)", user.email, token)

    async def on_after_request_verify(
        self, user: User, token: str, request: Request | None = None
    ) -> None:
        logger.info("Verification requested for %s (token=%s)", user.email, token)
