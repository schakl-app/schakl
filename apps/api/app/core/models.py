"""Core tenancy models: ``orgs``, ``memberships``, ``org_settings`` (CLAUDE.md §5, §7).

``orgs`` is the tenant table itself and ``users`` is global identity — neither is org-scoped.
``memberships`` and ``org_settings`` *are* org-scoped (RLS-forced in the migration).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.config import settings
from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class OrgStatus(StrEnum):
    """Org lifecycle (issue #26). ``deleted`` is the soft state; hard delete removes the row."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class Org(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant. Resolved from the request hostname (CLAUDE.md §5, §7).

    Hostname→org routing data (``custom_domain``) lives here, not on ``org_settings``:
    resolution runs *before* a tenant is known, so it can only read tables without RLS.
    Only a **verified** custom domain resolves — an unverified claim must never route
    traffic, or anyone could park another agency's domain on their own org (issue #26).
    """

    __tablename__ = "orgs"

    slug: Mapped[str] = mapped_column(String(63), unique=True, index=True, nullable=False)
    # Internal name; the *displayed* brand comes from org_settings.brand_name.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=OrgStatus.ACTIVE.value, server_default="active"
    )
    suspended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Stamped by the per-org export; hard delete refuses to run without a post-soft-delete export.
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    custom_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    custom_domain_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # A claim awaiting DNS TXT verification; promoted to custom_domain by the verify endpoint.
    pending_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    domain_verification_token: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Membership(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Links a (global) user to an org; what they may do lives in ``membership_roles`` (#19)."""

    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


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
    # DEPRECATED (expand/contract, issue #26): moved to orgs.custom_domain because resolution
    # runs before RLS is bound. Kept mapped so the column survives one release; drop next.
    custom_domain: Mapped[str | None] = mapped_column(
        String(255), nullable=True, unique=True, index=True
    )
    default_locale: Mapped[str] = mapped_column(
        String(10), nullable=False, default=settings.default_locale
    )
    # IANA timezone the org's local calendar runs in (CLAUDE.md §8): drives display of event
    # timestamps and the local-date reasoning in per-org cron (timesheet nudges, holiday top-up).
    # Validated against the zoneinfo database on write; falls back to the instance default.
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default=settings.default_timezone,
        server_default=settings.default_timezone,
    )
    # ISO 4217 code every money figure renders in (#124) — a business fact of the org, like the
    # timezone; validated against app.core.currency.ISO_4217 on write.
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EUR", server_default="EUR"
    )
    # Browser-tab title template (#97, #71 tier 2): free text with {page} / {brand} tokens,
    # e.g. "{page} · {brand}". NULL = the built-in i18n format. Branding, so it lives here.
    tab_title_template: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled_modules: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list
    )
    # Which permission-catalog keys this org's system roles have already been offered (issue
    # #19). A module that ships later adds keys, which the startup reconciler grants exactly
    # once — so a tenant who unticked a permission keeps it unticked.
    applied_permission_defaults: Mapped[list[str]] = mapped_column(
        ARRAY(String), nullable=False, default=list, server_default=text("'{}'")
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


class InstanceLicense(Base):
    """The installed product license (issue #137).

    Instance-level like :class:`InstanceAuditLog` — deliberately **not** org-scoped and
    **not** under RLS: one license covers the installation. A single row (``id = 1``) exists
    from migration time; besides the license key text it carries ``grace_started_at``, the
    bootstrap-grace clock that lets licensed modules enabled *before* licensing shipped keep
    working for a fixed window after upgrade instead of going read-only mid-flight.
    """

    __tablename__ = "instance_license"

    id: Mapped[int] = mapped_column(primary_key=True)
    license_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    grace_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    installed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    installed_by_email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class InstanceAuditLog(UUIDPrimaryKeyMixin, Base):
    """Audit trail for instance-level administration (issue #26).

    Instance-level like ``orgs``/``users`` — deliberately **not** org-scoped and **not** under
    RLS: it records the actions that manage or cross tenants (org lifecycle, impersonation,
    domain claims), and the trail must survive the org it describes. ``actor_email`` and
    ``org_slug`` are denormalized snapshots so a hard-deleted org's history stays readable.
    Only written through :mod:`app.core.instance.audit`; only read by the instance admin.
    """

    __tablename__ = "instance_audit_log"

    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_email: Mapped[str] = mapped_column(String(320), nullable=False)
    # e.g. "org.create", "org.suspend", "impersonate.start", "domain.claim".
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    org_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("orgs.id", ondelete="SET NULL"), nullable=True
    )
    org_slug: Mapped[str | None] = mapped_column(String(63), nullable=True)
    target_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
