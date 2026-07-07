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
