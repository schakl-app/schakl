"""Own-account operations that FastAPI Users' routers don't cover — today: change email.

The email address is the local sign-in identifier, so changing it is guarded like the other
credential changes (2FA disable, docs/TWOFACTOR.md): it costs the **current password**, never
just a session cookie. The bare ``PATCH /users/me`` deliberately ignores ``email``
(``schemas.UserUpdate``) so this is the only path. Mounted behind ``require_local_login`` —
on an OIDC-enforced org the address is the IdP's to manage, and rewriting it here would only
desync the account from its identity provider.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.auth.schemas import UserRead
from app.core.auth.users import current_active_user, get_user_manager
from app.db import get_session
from app.errors import AppError

logger = logging.getLogger("schakl.auth")

router = APIRouter()


class EmailChange(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


@router.post("/me/email", response_model=UserRead, name="users:change_email")
async def change_email(
    payload: EmailChange,
    user: User = Depends(current_active_user),
    user_manager=Depends(get_user_manager),  # noqa: ANN001 — FastAPI Users' provider
    session: AsyncSession = Depends(get_session),
) -> User:
    """Change the caller's own sign-in address. Costs the current password; the new address
    must be free (emails are unique case-insensitively and stored lowercase, like invites).
    Verification state resets — the new address has never been proven."""
    verified, _ = user_manager.password_helper.verify_and_update(
        payload.password, user.hashed_password
    )
    if not verified:
        raise AppError("password_incorrect", "errors.password_incorrect")

    # ``user`` is attached to FastAPI Users' own session; mutate this request's copy instead.
    db_user = await session.get(User, user.id)
    if db_user is None:  # pragma: no cover — the session cookie just resolved this user
        raise AppError("not_found", "errors.not_found", status_code=404)

    email = payload.email.lower()
    if email == db_user.email.lower():
        return db_user
    taken = await session.scalar(
        select(User.id).where(func.lower(User.email) == email, User.id != db_user.id)
    )
    if taken is not None:
        raise AppError("email_taken", "errors.email_taken", status_code=409)

    previous = db_user.email
    db_user.email = email
    db_user.is_verified = False
    await session.commit()
    logger.info("Email changed for user %s: %s -> %s", db_user.id, previous, email)
    return db_user
