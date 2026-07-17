"""Pydantic schemas for the auth/users routers (FastAPI Users)."""

from __future__ import annotations

import uuid

from fastapi_users import schemas


class UserRead(schemas.BaseUser[uuid.UUID]):
    full_name: str | None = None


class UserCreate(schemas.BaseUserCreate):
    full_name: str | None = None


class UserUpdate(schemas.BaseUserUpdate):
    full_name: str | None = None

    def create_update_dict(self):  # noqa: ANN201 — FastAPI Users' contract
        """Email changes go only through the password-guarded ``POST /users/me/email``
        (``account.py``). Left in the bare PATCH, a stolen session could redirect the account's
        sign-in address and reset the password to it — a full takeover for free."""
        values = super().create_update_dict()
        values.pop("email", None)
        return values
