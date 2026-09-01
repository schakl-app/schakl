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
    #: SE Ranking project (#300) — rankings, the site audit and AI-search visibility. The
    #: first source that is not Google, which is why the adapter protocol carries an ``auth``
    #: kind: this one rides one API key per *agency*, not a per-user OAuth grant.
    SERANKING = "seranking"
    #: Rank Math AI Visibility, read through the client's own WordPress (docs/WORDPRESS.md).
    #: The third ``auth`` kind and the reason there is one: its credential is per **website**,
    #: so it is resolved per *link* rather than per org or per user. A link of this source
    #: therefore requires ``website_id`` — see ``MarketingService.create_link``.
    RANKMATH = "rankmath"


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
    #: The client website this property measures. A client with two sites links each property to
    #: its own site, and the panel/tab group per website. Nullable — a link may stay client-level
    #: (no websites module, or a property spanning sites) — and SET NULL, not CASCADE: a removed
    #: website must not delete the link or its synced history, the link just goes client-level.
    website_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("websites.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    #: ``String(16)``, widened from 8 when ``rankmath`` arrived. ``"rankmath"`` is exactly
    #: eight characters, so it fit — and a schema that depends on a coincidence about the
    #: length of a brand name is a schema that breaks on the next source. Widening a varchar
    #: is a metadata-only change in Postgres, so the cost of not living on it was one line.
    source: Mapped[str] = mapped_column(String(16), nullable=False)
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
    #: For ``source="gads"``: the ``google_ads`` module's account row, which is the **authority**
    #: for which customer this is, which manager it is reached through and whose grant syncs it.
    #: NULL for every other source, and for a gads link on an instance that never enabled the
    #: module — in which case ``external_id``/``config`` below still answer, exactly as before.
    #:
    #: ``external_id`` stays populated either way, on purpose. It is what the panel prints and
    #: what ``deep_link`` builds from, and ``SourceMetrics.external_id`` is typed ``str``: a
    #: ``None`` there is a validation error, and company panels compose with no per-panel
    #: ``try``, so one unlinked Ads account would 500 the *whole* company hub rather than blank
    #: one tile. The join is the truth; this column is a display copy with a stated owner.
    google_ads_account_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("google_ads_accounts.id", ondelete="SET NULL"),
        nullable=True,
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
    #: What this client's dashboard measures a period against (#312) — an
    #: ``app.core.periods.ComparePeriod`` value. **NULL = follow the org default**, not
    #: "unfilled": the agency sets a house comparison once in Instellingen → Marketing and
    #: overrides it only where a client needs the other one (a site with no year of history
    #: behind it, a business with no season). Per client rather than per source, because one
    #: dashboard where GA4 reads against last year and Search Console against last month is not
    #: a screen anyone can summarise.
    compare: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: This client's keyword-positions settings for a report (#373) — the same shape as
    #: ``MarketingSettings.rankings``, validated by ``marketing.rankings.RankingSettings``.
    #: **NULL = follow the org default**, the ``compare`` idiom above: an agency decides once
    #: how it reports positions and overrides it for the client whose situation differs — the
    #: one with an SE Ranking project where the rest are on Search Console, or the one whose
    #: long tail is worth printing where most clients' is not.
    rankings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: How this client's report treats a client with several websites (#381), and which links
    #: it leaves out — ``marketing.reportsplit.ReportSettings``. **NULL = follow the org
    #: default**, the idiom above. ``exclude`` is only ever meaningful here, because a link id
    #: belongs to one client.
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


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
    #: The agency's SE Ranking API key (#300), same treatment. One key per agency covers every
    #: client project, which is why it belongs here and not on the link: an agency holds one
    #: SE Ranking account and links each client's project out of it.
    seranking_api_key_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: The house comparison every client's dashboard inherits (#312) — an
    #: ``app.core.periods.ComparePeriod`` value; NULL = the code default (``year``). An agency
    #: reports the same way for nearly all of its clients, so this is set once and overridden
    #: per client only where the client's own history says otherwise.
    default_compare: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: The house keyword-positions settings every client's report inherits (#373). NULL = the
    #: code defaults in ``marketing.rankings.RankingSettings``, which are what an agency that
    #: never opens this screen should get: **positions from whichever source the client
    #: actually has**, the terms they rank best for first, and nothing shown twice all month.
    rankings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: The house rule for a client with several websites (#381). NULL = the code default,
    #: ``per_website``: one named block per property inside each section, because that is the
    #: only answer that never quietly adds together two things a reader would have kept apart.
    report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: What a **client** is told each source is called (#446), ``{source: label}``. The vendor's
    #: name is the agency's supplier and not the client's business — "SE Ranking" on a client's
    #: dashboard is a name they never chose and a login they do not hold — so a portal login
    #: reads this label where a colleague reads the product name. Absent means the code's own
    #: vendor-free default for a keyed source and the product name for a Google source (the
    #: client's own account, which they know by that name). Never "Breik. Analytics" in code:
    #: the brand is the tenant's (§2, rule 4), so the tenant types it here.
    portal_source_labels: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
