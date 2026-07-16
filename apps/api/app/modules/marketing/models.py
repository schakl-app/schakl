"""``marketing`` models (epic #134): the account links (#132) + stored daily metrics (#133).

Two tables, both org-scoped + RLS-forced like every domain table:

- ``marketing_links`` — one row per (company, source, external property). ``source`` is an enum,
  not three columns, so a fourth source (Meta, LinkedIn) is a new value + adapter, not a schema
  redesign. ``display_name`` is **snapshotted at link time** — rendering the chips must never
  call Google (docs/PERFORMANCE.md). Unlinking *deactivates* (``active=False``) so historically
  synced metrics stay attributable; relinking reactivates. The link also carries its own sync
  health (``last_synced_at`` / ``last_error`` / ``backfill_done``) so a broken connection is a
  visible state, never silently stale charts.
- ``marketing_metrics_daily`` — one small row per link per day, an idempotent upsert keyed on
  ``(org_id, link_id, date)``. This is the two-tier strategy's tier 1: a deliberately tiny
  warehouse of daily aggregates that powers trends/deltas/overview/reports without burning
  Google quota on page views. Tier-2 drill-downs (top pages/queries/campaigns) are fetched live
  and never stored here.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base


class MarketingSource(StrEnum):
    """A linkable Google marketing data source.

    Deliberately *not* Tag Manager: GTM deploys tags, it has no marketeer-facing metrics of its
    own (the conversions it fires already come through GA4). A container link would buy a scope
    and a picker for zero data in a client-overview CRM. Extend here for Meta/LinkedIn later.
    """

    GA4 = "ga4"  # Google Analytics 4 property
    GSC = "gsc"  # Search Console site
    GADS = "gads"  # Google Ads account


class MarketingLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A company's link to one Google marketing property (#132)."""

    __tablename__ = "marketing_links"
    __table_args__ = (
        # Multiple links per source per company are allowed (two sites, two properties), so the
        # only uniqueness is the natural key of an *active* link — enforced in the service, not a
        # constraint, because a deactivated link may coexist with its reactivated successor.
        Index("ix_marketing_links_org_company", "org_id", "company_id"),
        Index("ix_marketing_links_org_source_active", "org_id", "source", "active"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(8), nullable=False)
    #: The provider's own id: GA4 "properties/123456789", GSC "sc-domain:acme.nl" or a URL,
    #: Ads "1234567890" (customer id, no dashes).
    external_id: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Snapshotted at link time — the chip renders from this, never from a live Google call.
    display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    #: Which connection's grant syncs this link. SET NULL (not CASCADE): a disconnected Google
    #: account must not delete the client's history — the link goes dormant and asks to reconnect.
    connection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_connections.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: Per-source extras: GA4 {currency, propertyType}; GSC {siteType}; Ads {currency,
    #: manager_id}. A JSONB blob, not columns — it differs per source and is display-only.
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # --- sync health (#133): a first-class visible state, never a silently stale chart ------- #
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: True once the 13-month backfill has completed, so a re-run doesn't restart it and the
    #: panel can say "eerste synchronisatie loopt" until it flips.
    backfill_done: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class MarketingMetricDaily(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """One link's aggregate metrics for one day — tier 1 of the sync (#133)."""

    __tablename__ = "marketing_metrics_daily"
    __table_args__ = (
        UniqueConstraint("org_id", "link_id", "date", name="uq_marketing_metrics_daily_key"),
        Index("ix_marketing_metrics_daily_link_date", "org_id", "link_id", "date"),
    )

    link_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("marketing_links.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    #: The day's metrics, source-shaped: GA4 {sessions, totalUsers, newUsers, keyEvents,
    #: conversions, engagementRate, totalRevenue, channels:{...}}; GSC {clicks, impressions,
    #: ctr, position}; Ads {cost, clicks, impressions, conversions, conversionsValue}.
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    #: The account's own currency (Ads/GA4 revenue) — may differ from org_settings.currency
    #: (#124); the display labels it, never converts it.
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MarketingCompanySettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Per-client marketing display preferences (issue #134).

    One row per company, created lazily on first change — **absence means the defaults**, so an
    install that never touches this table behaves exactly as before. Today it carries a single
    knob: whether GA4 **key events / conversions** are shown for this client. An agency reports
    conversions for some clients and not others; flipping this off drops ``keyEvents`` and its
    ``conversions`` alias from that client's panel, tab and the overview conversions column
    (gated server-side, so it never leaks in the payload). Scoped to GA4 — Google Ads keeps its
    own ``conversions``.
    """

    __tablename__ = "marketing_company_settings"
    __table_args__ = (
        UniqueConstraint("org_id", "company_id", name="uq_marketing_company_settings_company"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    #: Show GA4 key events / conversions for this client. Default on: it preserves the behaviour
    #: from before this setting existed (the metric was always visible).
    #: DEPRECATED (expand/contract, #192): one special case of the layout below. Honoured only
    #: where the layout has no tiles for GA4; drop the column in the release after next.
    show_key_events: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    #: The curated tab layout (#192): per source an ordered tile list (absence = hidden),
    #: per-tile label_i18n overrides, enabled drill-downs and the default charted metric.
    #: NULL = no curation, today's behaviour. Shape validated in modules/marketing/layout.py.
    layout: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class MarketingSettings(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Org-level (per-instance) marketing configuration — one row per org (issue #134).

    Holds the **Google Ads developer token**: a per-agency secret Google Ads requires on every
    call (docs/GOOGLE.md), which used to be instance env config
    (``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN``). Like the Google OAuth client secret and the OIDC
    secret it belongs in the database, encrypted and per-org, so a self-hoster sets it in
    Instellingen rather than editing the environment (CLAUDE.md §5 — build multi-tenant, deploy
    single-tenant). The env var stays a read-only fallback so an install that already set it keeps
    working until it's moved into settings.
    """

    __tablename__ = "marketing_settings"
    __table_args__ = (UniqueConstraint("org_id", name="uq_marketing_settings_org"),)

    #: Fernet-encrypted (``app.core.crypto``); never returned to a client — the API only reports
    #: whether it is configured, mirroring the Google client secret.
    ads_developer_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
