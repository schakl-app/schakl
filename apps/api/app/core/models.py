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
    Integer,
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
    # Ownership proven (TXT seen) but the domain not yet activated — the wizard's staged
    # middle state (#292): traffic/certificate DNS may still be propagating. Reset on claim.
    pending_domain_ownership_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The Cloudflare custom hostname provisioned for the *pending* domain once ownership is
    # proven (#292); promoted into cf_hostname_id at activation.
    pending_cf_hostname_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Cloudflare for SaaS (epic #199): the custom-hostname id registered for custom_domain when
    # the operator fronts the instance with Cloudflare. NULL everywhere the integration is off
    # (all self-host installs) — clearing the domain deletes the hostname by this id.
    cf_hostname_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # …and the DNS record id for this org's <slug>.<base_domain> subdomain, created at
    # provisioning time and removed when the org is terminated.
    cf_dns_record_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Custom-domain lifecycle (#291): the state Cloudflare + DNS last reported for
    # custom_domain. Written by the verify flow, the manual check endpoint and the daily
    # sweep; all NULL wherever the Cloudflare integration is off — a Traefik/Let's Encrypt
    # domain has no state to poll, so a verified domain there is live by definition
    # (`app.core.hosts.custom_domain_live`).
    cf_hostname_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cf_ssl_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Tri-state routing check (`app.core.domainflow.routing_check`): does traffic for
    # custom_domain still reach this instance? NULL = no verdict — never checked, the resolver
    # was unavailable, or the domain sits behind a proxy that neither DNS nor a fetch could
    # see through. Only positive evidence writes False; "we could not tell" must never read as
    # "the customer moved their DNS away".
    domain_dns_ok: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    domain_cert_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    domain_checked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The last reconciliation problem, Cloudflare's own words (or ours), for the settings UI.
    domain_check_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Alert dedup for the sweep: a fingerprint of the state the org was last mailed about,
    # so a broken domain is reported once per distinct problem, not once per day.
    domain_alerted_for: Mapped[str | None] = mapped_column(String(120), nullable=True)

    # Cloud plan (epic #199, issue #200 slice): NULL on self-host / unmanaged orgs. One of
    # "trial" (expires at trial_ends_at → suspended by the cloud cron), "standard" (billing
    # drives suspension over the provisioning API) or "unlimited" (never expires). This is
    # *platform* billing state — nothing to do with the tenant's own `subscriptions` module.
    plan: Mapped[str | None] = mapped_column(String(20), nullable=True)
    trial_ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # Per-org end date (epic #199). NULL = **unlimited**, and the lifecycle sweep skips the org
    # entirely — the default for a column that eventually destroys data must be "never".
    # Past ends_at: warned for grace_days, then suspended for retention_days, then terminated.
    # grace_days / retention_days are NULL to inherit the instance defaults.
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    grace_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    retention_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Where the sweep last left this org, so a transition fires once instead of every run.
    lifecycle_stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )
    lifecycle_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # May this org send through the operator's own transport — the cloud "included e-mail"
    # (epic #199)? An *entitlement*, so it lives beside `plan` on `orgs` and is written only
    # from the instance surface; the tenant chooses whether to use it, never whether they
    # have it. True by default (what an org gets when nobody said otherwise, and what every
    # pre-existing org already behaved as); False leaves the org bring-your-own-transport,
    # exactly as if the instance had none. Inert wherever the instance transport is unset.
    email_included: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


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
    # The installable-app icon source (#198): a square raster the PWA manifest and the
    # apple-touch-icon derive their size variants from. A different asset from the favicon
    # (a 16px tab glyph makes an ugly home-screen tile), still runtime per-tenant.
    app_icon_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
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
    # ISO 3166-1 alpha-2 the org operates from — the country a value is read *in* when the
    # value itself doesn't say. Today that is phone parsing: a spreadsheet writes `0612345678`,
    # not `+31612345678`, and `phonenumbers` cannot resolve a national number without a region
    # (#256 required E.164, which no real client list carries). Also the default for a new
    # company's country. A record's own country always wins over this.
    default_country: Mapped[str] = mapped_column(
        String(2), nullable=False, default="NL", server_default="NL"
    )
    # Browser-tab title template (#97, #71 tier 2): free text with {page} / {brand} tokens,
    # e.g. "{page} · {brand}". NULL = the built-in i18n format. Branding, so it lives here.
    tab_title_template: Mapped[str | None] = mapped_column(String(120), nullable=True)
    enabled_modules: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False, default=list)
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


class NavPref(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Sidebar navigation layout (#169) — DashboardPref's shape, one problem over.

    One row per user, plus at most one row with ``user_id IS NULL`` — the org's default that
    admins curate (Instellingen → Navigatie). A user without their own row inherits the
    default; nobody's row hides the fixed core items (Dashboard, Agenda, Instellingen), only
    module-contributed nav is customizable.
    """

    __tablename__ = "nav_prefs"
    __table_args__ = (
        UniqueConstraint("org_id", "user_id"),
        # Postgres treats NULLs as distinct, so the template row needs its own partial guard.
        Index(
            "uq_nav_prefs_org_default",
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
    # Ordered ``{"key": ..., "hidden": bool}`` entries; unknown keys are ignored, and a nav
    # item absent from the list (a module enabled later) falls back to its declared position.
    # The org-default row may instead hold ``{"items": [...], "groups": [...]}`` where an item
    # or group carries a tenant ``label`` (``{nl, en}``) — the org's own name for a nav entry
    # or group heading (#169). A legacy plain list is read as items-only (no migration —
    # JSONB), and a personal row stays a plain ``{key, hidden}`` list (labels are org config).
    items: Mapped[list[dict] | dict] = mapped_column(
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
    prefs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")


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


class InstanceAdmin(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A delegated instance operator and exactly what they may do (issue #26).

    Instance-level like ``orgs``/``users`` and deliberately **not** under RLS: it decides who
    may cross tenants, so it is read before any tenant is bound. The owner principal stays
    ``users.is_superuser`` and holds every capability implicitly; a row here is the *second*
    principal, holding only what it was granted.

    ``granted_by_email`` is snapshotted beside the FK for §16's reason: the trail of who
    delegated cross-tenant access must outlive the account that did it.
    """

    __tablename__ = "instance_admins"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    #: Catalog keys from ``app.core.instance.capabilities``. An empty list is valid and means
    #: "can sign in to the console and see nothing" — the safe default for a half-finished
    #: invite, which must never over-grant.
    capabilities: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    granted_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    granted_by_email: Mapped[str] = mapped_column(String(320), nullable=False)


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
