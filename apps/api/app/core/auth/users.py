"""FastAPI Users wiring: user DB, manager provider, and shared auth dependencies."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

from fastapi import Depends
from fastapi_users import FastAPIUsers
from fastapi_users.db import SQLAlchemyUserDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.backend import auth_backend
from app.core.auth.manager import UserManager
from app.core.auth.models import User
from app.db import get_session


async def get_user_db(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[SQLAlchemyUserDatabase, None]:
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> AsyncGenerator[UserManager, None]:
    yield UserManager(user_db)


fastapi_users = FastAPIUsers[User, uuid.UUID](get_user_manager, [auth_backend])

# Shared dependency used across the app (see core/tenancy.require_context).
current_active_user = fastapi_users.current_user(active=True)
# Optional variant: yields ``None`` instead of 401 when there is no session, so ``require_context``
# can fall through to API-key authentication (#20) rather than rejecting the request outright.
current_active_user_optional = fastapi_users.current_user(active=True, optional=True)
