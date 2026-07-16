"""Pydantic schemas for the marketing module (epic #134)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.marketing.models import MarketingSource


# --- links (#132) ---------------------------------------------------------------------------- #
class WebsiteRef(BaseModel):
    """A client website a link can attach to — id + display name (the domain), nothing more."""

    id: uuid.UUID
    name: str


class LinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    #: The client website this property measures — None = a client-level link.
    website_id: uuid.UUID | None = None
    website_name: str | None = None
    source: MarketingSource
    external_id: str
    display_name: str
    config: dict = Field(default_factory=dict)
    active: bool
    #: Sync health so the chip / panel can label a broken link instead of pretending it synced.
    last_synced_at: datetime | None = None
    last_error: str | None = None
    backfill_done: bool = False
    #: Whether the syncing connection still exists + is active (else: "reconnect Google").
    connection_ok: bool = True


class LinkCreate(BaseModel):
    company_id: uuid.UUID
    #: Attach the link to one of the client's websites (validated against the company); omit for
    #: a client-level link.
    website_id: uuid.UUID | None = None
    source: MarketingSource
    external_id: str = Field(min_length=1, max_length=512)
    display_name: str = Field(min_length=1, max_length=512)
    config: dict = Field(default_factory=dict)


# --- pickers (#132) -------------------------------------------------------------------------- #
class AvailableAccount(BaseModel):
    external_id: str
    display_name: str
    #: The Google account this belongs to — the combobox hint when several are connected.
    account_hint: str | None = None
    config: dict = Field(default_factory=dict)
    #: Already linked to *this* company, so the picker can mark/skip it.
    already_linked: bool = False


class AccountsResponse(BaseModel):
    """A picker's option list plus the state that lets an empty list *teach* (#132)."""

    source: MarketingSource
    #: A Google connection exists for the caller / an admin has connected one.
    connected: bool = False
    #: The connection carries this source's scope; else the user must reconnect to add it.
    has_scope: bool = False
    #: Source-specific prerequisite met (Ads: a developer token is configured).
    configured: bool = True
    accounts: list[AvailableAccount] = Field(default_factory=list)
    #: A live-fetch failure (revoked token, quota) — a clear reconnect message, not a stack trace.
    error: str | None = None
    #: The ``/google/oauth/connect`` query flag that adds this scope (for the connect deep-link).
    connect_flag: str = ""


# --- metrics (#133): panel + tab ------------------------------------------------------------- #
class KpiValue(BaseModel):
    current: float = 0.0
    previous: float = 0.0
    #: None when there is no prior period to compare against (a brand-new link).
    delta_pct: float | None = None
    #: True for metrics where down is good (avg position), so the web colours the delta right.
    lower_is_better: bool = False


class SeriesData(BaseModel):
    dates: list[date] = Field(default_factory=list)
    #: metric key -> one value per date (same length/order as ``dates``).
    metrics: dict[str, list[float]] = Field(default_factory=dict)


class SourceMetrics(BaseModel):
    link_id: uuid.UUID
    source: MarketingSource
    display_name: str
    external_id: str
    #: The client website this link measures (None = client-level) — the tab groups on it.
    website_id: uuid.UUID | None = None
    website_name: str | None = None
    #: "ok" (synced), "pending" (backfill running / never synced), "error" (link's sync failed),
    #: "disconnected" (its Google connection is gone/errored).
    health: str = "pending"
    last_error: str | None = None
    last_synced_at: datetime | None = None
    currency: str | None = None
    deep_link: str = ""
    primary_metric: str = ""
    kpis: dict[str, KpiValue] = Field(default_factory=dict)
    series: SeriesData = Field(default_factory=SeriesData)
    #: GA4 only: period sessions by acquisition channel, for the split.
    channels: dict[str, float] | None = None
    #: The ordered, *visible* tile keys after the client's layout applied (#192). Hidden tiles
    #: are already absent from ``kpis``/``series`` — this carries the curated order.
    tiles: list[str] = Field(default_factory=list)
    #: Per-tile label overrides, ``{metric: {locale: label}}`` (#192) — tenant data, so every
    #: consumer (web, MCP) shows the client's naming.
    tile_labels: dict[str, dict[str, str]] = Field(default_factory=dict)
    #: The enabled drill-down kinds after the layout applied (#192).
    drilldowns: list[str] = Field(default_factory=list)


class CompanyMarketing(BaseModel):
    """The payload behind the company panel (30d) and the marketing tab (any range)."""

    company_id: uuid.UUID
    range_days: int
    sources: list[SourceMetrics] = Field(default_factory=list)
    #: No Google connection anywhere in the org — the panel teaches how to connect.
    needs_connection: bool = False
    #: A connection exists but the caller may not manage links (a member) — name who can.
    can_manage: bool = False
    #: Whether GA4 key events / conversions are shown for this client (#134). When False the
    #: GA4 sources above already omit those metrics; the flag lets the UI render the toggle.
    show_key_events: bool = True
    #: The stored layout (#192), for the tab's edit mode — present only for a caller who may
    #: manage it (``can_manage``); ``None`` = no curation.
    layout: dict | None = None
    #: The client's websites, so the link pickers can attach a new link to one and the tab can
    #: label its groups. Empty when the client has none (links stay client-level).
    websites: list[WebsiteRef] = Field(default_factory=list)


# --- per-client settings (#134, layout #192) -------------------------------------------------- #
class CompanySettingsUpdate(BaseModel):
    """Per-client marketing preferences. Both fields optional: send what changes.

    ``layout`` replaces the stored layout wholesale (``{"sources": {}}`` clears it); the
    legacy ``show_key_events`` keeps working during the expand release (#192).
    """

    show_key_events: bool | None = None
    layout: dict | None = None


class CompanySettingsRead(BaseModel):
    """A client's marketing preferences, echoed back after a change."""

    company_id: uuid.UUID
    show_key_events: bool
    layout: dict | None = None


# --- org-level settings (#134) --------------------------------------------------------------- #
class MarketingSettingsRead(BaseModel):
    """The org's marketing settings. The Ads developer token is write-only — like the Google
    client secret, the API reports only whether one is configured, never the value."""

    ads_developer_token_configured: bool = False
    #: True when the deprecated ``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var is set — the fallback
    #: still used when no token is stored, so the UI can say "using the environment value".
    env_ads_token_configured: bool = False


class MarketingSettingsWrite(BaseModel):
    #: The Google Ads developer token. Empty/omitted keeps the stored one (the Google-client-secret
    #: rule); the API never plays it back.
    ads_developer_token: str | None = Field(default=None, max_length=1024)


class DrilldownRowOut(BaseModel):
    label: str
    href: str | None = None
    metrics: dict[str, float] = Field(default_factory=dict)


class DrilldownResponse(BaseModel):
    source: MarketingSource
    kind: str
    columns: list[str] = Field(default_factory=list)
    rows: list[DrilldownRowOut] = Field(default_factory=list)
    #: False + a reason when the live fetch can't run (no scope, Ads token, revoked grant).
    available: bool = True
    unavailable_reason: str | None = None
    deep_link: str = ""


# --- cross-client overview (#133) ------------------------------------------------------------ #
class OverviewRow(BaseModel):
    company_id: uuid.UUID
    company_name: str
    sources_present: list[MarketingSource] = Field(default_factory=list)
    #: Headline metrics with their period-over-period deltas (sessions, clicks, position, cost,
    #: conversions). Absent when the client has no link feeding that metric — and ``conversions``
    #: is also withheld when this client's ``show_key_events`` is off (#134).
    metrics: dict[str, KpiValue] = Field(default_factory=dict)
    #: Whether GA4 key events / conversions are shown for this client (drives the grid's toggle).
    show_key_events: bool = True


class OverviewResponse(BaseModel):
    range_days: int
    rows: list[OverviewRow] = Field(default_factory=list)
    total: int = 0
