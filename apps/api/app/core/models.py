"""Core tenancy models: ``orgs``, ``memberships``, ``org_settings`` (CLAUDE.md §5, §7).

``orgs`` is the tenant table itself and ``users`` is global identity — neither is org-scoped.
``memberships`` and ``org_settings`` *are* org-scoped (RLS-forced in the migration).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Index, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
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
    # Hide the brand name text next to the logo (for logos that already contain the name).
    show_brand_name: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
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


class DashboardPref(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """My Day layout: which widgets, in which order (CLAUDE.md §10 dashboard).

    One row per user, plus at most one row with ``user_id IS NULL`` — the org's default
    template that managers curate. A user without their own row inherits the template.
    """

    __tablename__ = "dashboard_prefs"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id"),
        # Postgres treats NULLs as distinct, so the template row needs its own partial guard.
        Index(
            "uq_dashboard_prefs_org_default",
            "org_id",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    # Ordered widget keys (e.g. ["time.today", "tasks.my_open"]); unknown keys are ignored.
    widgets: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class UserPref(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-user personal preferences — a free JSONB blob keyed by feature namespace
    (e.g. ``{"time": {"week_view": "work"}}``). One row per (org, user). Personal, in-view
    settings that only touch the user's own experience (CLAUDE.md UX §6), distinct from the
    org-wide ``org_settings`` and the dashboard template.
    """

    __tablename__ = "user_prefs"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    prefs: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )
