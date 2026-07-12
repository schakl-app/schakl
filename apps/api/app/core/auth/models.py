"""The ``User`` model — global identity (CLAUDE.md §5).

Users are **not** org-scoped: one identity can be a member of several orgs via ``memberships``.
So this table has no ``org_id`` and no RLS; tenant scoping happens on domain tables and on the
membership lookup. FastAPI Users owns the auth columns (email, hashed_password, flags).
"""

from __future__ import annotations

from fastapi_users.db import SQLAlchemyBaseUserTableUUID
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import TimestampMixin
from app.db import Base


class User(SQLAlchemyBaseUserTableUUID, TimestampMixin, Base):
    __tablename__ = "users"

    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Profile picture (#122): the IdP's `picture` claim, refreshed each SSO login…
    #: TEXT, not a sized VARCHAR: Google's signed picture URLs run past 1,024 characters, and
    #: an overflow here failed the login itself (the write shares the callback's transaction).
    oidc_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: …and the personal override (an uploaded file's URL, or any image URL). The effective
    #: avatar is custom → oidc → None (initials).
    custom_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Personal display-language preference (CLAUDE.md §8). NULL → fall back to the org default.
    # It follows the user across devices: seeded into the PARAGLIDE_LOCALE cookie on login.
    locale: Mapped[str | None] = mapped_column(String(10), nullable=True)
