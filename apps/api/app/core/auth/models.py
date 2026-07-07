"""The ``User`` model — global identity (CLAUDE.md §5).

Users are **not** org-scoped: one identity can be a member of several orgs via ``memberships``.
So this table has no ``org_id`` and no RLS; tenant scoping happens on domain tables and on the
membership lookup. FastAPI Users owns the auth columns (email, hashed_password, flags).
"""

from __future__ import annotations

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import TimestampMixin
from app.db import Base


class User(SQLAlchemyBaseUserTableUUID, TimestampMixin, Base):
    __tablename__ = "users"

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Personal display-language preference (CLAUDE.md §8). NULL → fall back to the org default.
    # It follows the user across devices: seeded into the PARAGLIDE_LOCALE cookie on login.
    locale: Mapped[str | None] = mapped_column(String(10), nullable=True)
