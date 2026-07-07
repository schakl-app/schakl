"""Core tenancy models: ``orgs``, ``memberships``, ``org_settings`` (CLAUDE.md §5, §7).

``orgs`` is the tenant table itself and ``users`` is global identity — neither is org-scoped.
``memberships`` and ``org_settings`` *are* org-scoped (RLS-forced in the migration).
"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.core.roles import Role
from app.db import Base


class Org(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. Resolved from the request hostname (CLAUDE.md §5, §7)."""

    __tablename__ = "orgs"

    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True, nullable=False)
    # Internal name; the *displayed* brand comes from org_settings.brand_name.
    name: Mapped[str] = mapped_column(String(255), nullable=False)


class Membership(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Links a (global) user to an org with a role."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False, default=Role.MEMBER.value)


class OrgSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-org white-label settings applied at runtime (CLAUDE.md §7). One row per org."""

    __tablename__ = "org_settings"
    __table_args__ = (UniqueConstraint("org_id"),)

    brand_name: Mapped[str] = mapped_column(String(255), nullable=False)
    logo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    favicon_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    primary_color: Mapped[str] = mapped_column(String(32), nullable=False, default="#4f46e5")
    accent_color: Mapped[str] = mapped_column(String(32), nullable=False, default="#0ea5e9")
    custom_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    default_locale: Mapped[str] = mapped_column(
        String(10), nullable=False, default=settings.default_locale
    )
    enabled_modules: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
