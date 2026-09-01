"""Pydantic schemas for the marketing module (epic #134)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.periods import ComparePeriod
from app.modules.marketing.models import MarketingSource
from app.modules.marketing.rankings import (
    DEFAULT_LIMIT,
    DEFAULT_MAX_POSITION,
    DEFAULT_MIN_IMPRESSIONS,
    RankingSource,
)
from app.modules.marketing.reportsplit import ReportSplit


# --- links (#132) ---------------------------------------------------------------------------- #
class WebsiteRef(BaseModel):
    """A client website a link can attach to — id + display name (the domain), nothing more."""

    id: uuid.UUID
    name: str


class ConnectionOwner(BaseModel):
    """Whose Google grant a link (or an available account) rides on.

    A marketing link syncs through **one person's** connection, and every colleague looking at
    that client sees the result without any hint of whose it is — so a working link reads as
    "connected" to its owner and as nothing in particular to everyone else, and the natural
    reaction is to connect a second account for the same data. Naming the owner is also the
    only warning anyone gets that the link dies the day that person leaves.

    Both the person and the Google account: they are routinely different addresses, and which
    Google account holds the Ads access is exactly what the next person needs to know.
    """

    user_id: uuid.UUID
    #: The colleague's own name (their login e-mail when they have no name set).
    name: str
    #: The connected Google account.
    email: str
    #: This is the caller's own connection — the UI says "via jou", not "via <naam>".
    is_me: bool = False


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
    #: Whose Google grant syncs this link — ``None`` only for a link whose connection is gone.
    connection_owner: ConnectionOwner | None = None


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
    #: Colleagues whose connection already reaches this source. A picker that only knows about
    #: the *caller's* grant tells the second person in the agency "not connected" about accounts
    #: their colleague linked minutes ago, so they connect again — this is what turns that empty
    #: state into "already connected via X; connect your own to pick accounts yourself".
    connected_via: list[ConnectionOwner] = Field(default_factory=list)
    #: For a **site-key** source only: which prerequisite is the first unmet one
    #: (``app.core.wordpress``'s ``STAGE_*``), including ``ready``. ``None`` for every other
    #: source, which has no per-website setup to be partway through.
    #:
    #: It exists because ``configured`` is one boolean over six different jobs for three
    #: different people (#435): "no credential yet" and "the credential was refused" answered
    #: identically, so the picker drew the first sentence over both, and "Rank Math is not
    #: installed" and "this client has no brand yet" were an empty list. A stage is what makes
    #: a *next step* drawable — §9's rule that `details` carries what a translated `message`
    #: structurally cannot.
    setup_stage: str | None = None
    #: WordPress's own words about the refusal above, carried as a quote and never translated
    #: (§9: the provider's own prose never becomes the envelope's `message`).
    setup_detail: str | None = None
    #: Deep links into the client's own ``wp-admin`` for the stage above — ``app_passwords``,
    #: ``plugins``, ``ai_visibility``. Empty where we hold no address to build one from.
    setup_links: dict[str, str] = Field(default_factory=dict)


# --- metrics (#133): panel + tab ------------------------------------------------------------- #
class MarketingCompareWindow(BaseModel):
    """Which two spans a screen's deltas actually measured (#312).

    Every payload that carries a ``delta_pct`` carries this, because a percentage with no named
    denominator is the thing this issue was filed about: "t.o.v. vorige periode" was a label the
    screen could print whatever it had compared, and it printed it while the same client's PDF
    said "vorig jaar". Both spans travel, not just the comparison one — the web names the period
    a delta is *against*, and a screen that can only say one of the two can never be checked.

    Dates rather than a mode name: the mode is configuration, the dates are what happened. A
    reader who sees "t.o.v. jul 2025" needs no vocabulary at all.
    """

    mode: ComparePeriod
    #: The period the numbers themselves cover (``range_days`` back from yesterday).
    current_start: date
    current_end: date
    #: The span those numbers were measured against.
    start: date
    end: date

    def spans(self) -> list[tuple[date, date]]:
        """Both windows, for a reader that must fetch exactly these days and nothing between.

        Two entries even when they are adjacent (``previous`` mode): a caller that special-cased
        the contiguous shape would be one config change away from silently reading a year of
        rows it does not use.
        """
        return [(self.current_start, self.current_end), (self.start, self.end)]


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
    #: Whose Google grant syncs this source (``None`` when its connection is gone) — the panel
    #: and the tab name it, so "disconnected" points at a person instead of at nobody. Never
    #: sent to a portal login (#448): whose credential feeds a client's numbers is the agency's
    #: working surface, exactly as ``connections`` already were (#411).
    connection_owner: ConnectionOwner | None = None
    currency: str | None = None
    #: The vendor console, for a colleague. Empty for a portal login (#447): the link lands on
    #: the *agency's* account in the supplier's product, which a client can neither open nor
    #: should be told the name of — #253's control that always refuses, printed beside the
    #: supplier's name.
    deep_link: str = ""
    #: What this reader calls the source (#446). ``None`` for staff — the web prints the product
    #: name — and always set for a portal login: the tenant's own label from Instellingen →
    #: Marketing, else a vendor-free default. Resolved here rather than in the browser so the
    #: widget, the tab and an MCP client all answer with the same word.
    label: str | None = None
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
    #: This source is hidden from the client's dashboard (#192). Only ever ``True`` in the payload
    #: for a manager (the portal/client never receives a hidden source at all); it lets edit mode
    #: show the section with a re-enable toggle.
    hidden: bool = False


class MarketingConnection(BaseModel):
    """A client attachment that is **not** a metrics source (#411).

    Tag Manager is the reason this shape exists, and the reason it is a *second* list rather
    than a sixth ``MarketingSource``. The enum's own docstring already settled the question and
    was right: a container has no marketeer-facing numbers of its own — no adapter, no daily
    rows, no KPI row, no drill-down — and the conversions it fires arrive through GA4 already.
    Making it a source to reuse one picker would have bought a value that
    ``METRICS_BY_SOURCE``, ``SCOPE_BY_SOURCE``, ``primary_metric``, ``aggregate``, the overview
    grid, the report sections and the nightly sync all have to be taught to say nothing about —
    and would have put a row on a client's dashboard that draws no numbers, which is exactly
    what reads as broken.

    So the connect control offers two labelled lists and this is the second one. There is no
    marketing row behind it: the ``gtm_containers`` row *is* the link, which is a stronger form
    of #338's rule than mirroring — two rows that cannot disagree because there is only one.
    """

    #: Which integration this is. A string rather than an enum because the vocabulary belongs to
    #: whoever contributes the connection, not to this module (§6).
    kind: str = "gtm"
    #: The contributing module's own row id — what its screens address the connection by.
    id: uuid.UUID
    #: What anybody quotes: ``GTM-XXXXXXX``.
    external_id: str
    name: str
    #: The contributor's own health word, rendered by the same badge the sources use.
    status: str = "ok"
    last_error: str | None = None
    #: Staged and never published — the number this row exists to carry onto the hub (#411).
    #: A change staged weeks ago is how a client's tracking quietly stops being what they were
    #: told it is, and nobody opens a container they have no reason to open.
    pending_changes: int = 0
    #: How much is live right now, so "12 tags, 3 staged" is one sentence.
    live_count: int = 0
    observed_at: datetime | None = None
    #: Into the provider's own console.
    deep_link: str = ""
    #: The in-app screen that works on it.
    href: str = ""


class CompanyMarketing(BaseModel):
    """The payload behind the company panel (30d) and the marketing tab (any range)."""

    company_id: uuid.UUID
    range_days: int
    #: The two spans every ``delta_pct`` below was computed from (#312) — so the screen names
    #: the period it compared against instead of an unfalsifiable "vorige periode".
    compare: MarketingCompareWindow
    #: What is *stored* for this client: ``None`` = follow the org default. Present only for a
    #: caller who may manage it (like ``layout``), because it is the value the editor's select
    #: binds to and "inherit" must stay distinguishable from "explicitly set to the default".
    compare_setting: ComparePeriod | None = None
    #: The org's house default, so the editor's inherit option can name what it inherits
    #: ("Volg standaard (vorig jaar)") rather than being a blank the user has to go and look up.
    compare_default: ComparePeriod = ComparePeriod.YEAR
    sources: list[SourceMetrics] = Field(default_factory=list)
    #: Attachments that carry no metrics (#411) — Tag Manager today. Filled only where a
    #: screen draws them (the company panel), so the tab, `/marketing` and the client's
    #: portal widget cost exactly what they cost before.
    connections: list[MarketingConnection] = Field(default_factory=list)
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
    #: The latest published report's own words about this client, keyed by report section, plus
    #: the period they describe (#300, ``app/core/narratives.py``). A dashboard is a table until
    #: somebody explains it, and the agency already wrote that explanation once — so the panel,
    #: the tab and the client's portal widget show it beside the numbers rather than only in a
    #: PDF once a month. ``None`` whenever there is nothing to borrow: reporting not installed,
    #: not licensed, or no published report yet, and every screen renders as it did before.
    narrative: dict | None = None


# --- keyword positions (#373) ----------------------------------------------------------------- #
class RankingSettingsWrite(BaseModel):
    """How keyword positions are reported — the agency's house rule, or one client's own.

    Every field optional and merged over what it inherits, so raising the house limit reaches
    every client who never set one (``marketing.rankings.parse``'s diff rule).
    """

    source: RankingSource | None = None
    limit: int | None = Field(default=None, ge=1, le=200)
    min_impressions: int | None = Field(default=None, ge=0, le=10_000)
    max_position: int | None = Field(default=None, ge=3, le=100)
    grouped: bool | None = None
    show_landing_pages: bool | None = None


class RankingSettingsRead(BaseModel):
    """The resolved answer — never nulls, so no screen has to re-derive inheritance."""

    source: RankingSource = RankingSource.AUTO
    limit: int = DEFAULT_LIMIT
    min_impressions: int = DEFAULT_MIN_IMPRESSIONS
    max_position: int = DEFAULT_MAX_POSITION
    grouped: bool = True
    show_landing_pages: bool = True


# --- reporting a client with several websites (#381) ------------------------------------------ #
class ReportSplitSettingsWrite(BaseModel):
    """How a client's report treats a client with more than one website.

    Every field optional and merged over what it inherits — ``RankingSettingsWrite``'s rule, so
    an agency raising the house default reaches every client who never set one.
    """

    split: ReportSplit | None = None
    #: Marketing links this client's report leaves out. Meaningful per client only, because a
    #: link id is; an **exclusion** rather than an inclusion so linking a new property adds it
    #: to the report, which is what linking a property means.
    exclude: list[uuid.UUID] | None = None


class LinkBrief(BaseModel):
    """A link, as much of it as a chooser needs: what it is and what to call it.

    Deliberately not :class:`LinkRead`. This rides the per-client settings payload that a
    reporting screen already loads, and a screen offering "leave this property out of the
    report" needs a name and an id — not sync health, not a connection owner, and not a config
    blob (`docs/PERFORMANCE.md`: a row carries only what its screen draws).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source: MarketingSource
    display_name: str


class ReportSplitSettingsRead(BaseModel):
    """The resolved answer — never nulls, so no screen re-derives inheritance."""

    split: ReportSplit = ReportSplit.PER_WEBSITE
    exclude: list[uuid.UUID] = Field(default_factory=list)


# --- per-client settings (#134, layout #192) -------------------------------------------------- #
class CompanySettingsUpdate(BaseModel):
    """Per-client marketing preferences. Every field optional: send what changes.

    ``layout`` replaces the stored layout wholesale (``{"sources": {}}`` clears it); the
    legacy ``show_key_events`` keeps working during the expand release (#192).

    ``compare`` follows the bulk-edit rule (CLAUDE.md §18): **absent means leave alone, an
    explicit ``null`` means clear back to the org default**. It has to, because ``None`` is a
    meaningful stored value here — the dashboard's select posts "volg standaard" as a real
    choice, and a payload that could not express it would leave a client pinned to whatever was
    set once, forever. The service reads ``model_fields_set`` to tell the two apart.

    ``rankings`` follows the same rule for the same reason (#373).
    """

    show_key_events: bool | None = None
    layout: dict | None = None
    compare: ComparePeriod | None = None
    rankings: RankingSettingsWrite | None = None
    #: How this client's report handles their several websites (#381). Same rule again.
    report: ReportSplitSettingsWrite | None = None


class CompanySettingsRead(BaseModel):
    """A client's marketing preferences, echoed back after a change."""

    company_id: uuid.UUID
    show_key_events: bool
    layout: dict | None = None
    #: The stored override; ``None`` = follows the org default (which ``compare_resolved`` is).
    compare: ComparePeriod | None = None
    #: What that resolves to today — the editor shows the stored value, the screen shows this.
    compare_resolved: ComparePeriod = ComparePeriod.YEAR
    #: The stored keyword-positions override; ``None`` = follows the org's (#373).
    rankings: dict | None = None
    #: …and what that resolves to, which is what a run will actually do.
    rankings_resolved: RankingSettingsRead = Field(default_factory=RankingSettingsRead)
    #: The sources this client actually has an active link to. What lets a section picker say
    #: whether a section will have anything in it rather than listing nine names and leaving
    #: the reader to guess which are wired up (#373).
    linked_sources: list[MarketingSource] = Field(default_factory=list)
    #: Which source the keyword-positions section will read, after the setting *and* what is
    #: linked. ``None`` = this client gets no rankings section — resolved here so the screen
    #: cannot promise one the run then drops.
    keyword_source: RankingSource | None = None
    #: The stored per-website override; ``None`` = follows the org's (#381).
    report: dict | None = None
    #: …and what that resolves to, which is what a run will actually do.
    report_resolved: ReportSplitSettingsRead = Field(default_factory=ReportSplitSettingsRead)
    #: The client's active links, so the screen can offer "leave this property out" by name
    #: rather than by id. Only ever a handful, and the screen already loads this row.
    links: list[LinkBrief] = Field(default_factory=list)


# --- org-level settings (#134) --------------------------------------------------------------- #
class MarketingSettingsRead(BaseModel):
    """The org's marketing settings. The Ads developer token is write-only — like the Google
    client secret, the API reports only whether one is configured, never the value."""

    ads_developer_token_configured: bool = False
    #: True when the deprecated ``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var is set — the fallback
    #: still used when no token is stored, so the UI can say "using the environment value".
    env_ads_token_configured: bool = False
    #: Whether the agency's SE Ranking API key is stored (#300). One key covers every client
    #: project; the value itself is never returned, like the token above.
    seranking_api_key_configured: bool = False
    #: The house comparison every client's dashboard inherits (#312) — always resolved, never
    #: null: a settings screen offering "niets gekozen" beside two real options would be a third
    #: state nobody means. A client overrides it on their own dashboard.
    default_compare: ComparePeriod = ComparePeriod.YEAR
    #: The house keyword-positions settings every client's report inherits (#373). Always
    #: resolved, for the same reason as ``default_compare``.
    rankings: RankingSettingsRead = Field(default_factory=RankingSettingsRead)
    #: The house rule for a client with several websites (#381). Always resolved, same reason.
    report: ReportSplitSettingsRead = Field(default_factory=ReportSplitSettingsRead)
    #: The tenant's own client-facing source names (#446) — only the ones set; the defaults
    #: are the code's and the screen prints them as placeholders.
    portal_source_labels: dict[str, str] = Field(default_factory=dict)


class MarketingSettingsWrite(BaseModel):
    #: The Google Ads developer token. Empty/omitted keeps the stored one (the Google-client-secret
    #: rule); the API never plays it back.
    ads_developer_token: str | None = Field(default=None, max_length=1024)
    #: The agency's SE Ranking API key (#300). Same write-only rule.
    seranking_api_key: str | None = Field(default=None, max_length=1024)
    #: The org's default comparison (#312). Omitted keeps the stored one — unlike the per-client
    #: field there is nothing above this to inherit from, so ``null`` has no second meaning here.
    default_compare: ComparePeriod | None = None
    #: The house keyword-positions settings (#373). Same rule: omitted keeps the stored one.
    rankings: RankingSettingsWrite | None = None
    #: The house per-website rule (#381). Same rule again.
    report: ReportSplitSettingsWrite | None = None
    #: What a client is told each source is called (#446), ``{source: label}``. Omitted keeps the
    #: stored map; a present map is merged key by key, and an empty label clears that key back
    #: to the default — so the settings form posts every source it draws and nothing else.
    portal_source_labels: dict[str, str] | None = None

    @field_validator("portal_source_labels")
    @classmethod
    def _known_sources_only(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        known = {source.value for source in MarketingSource}
        out: dict[str, str] = {}
        for key, label in value.items():
            if key not in known:
                raise ValueError(f"unknown marketing source: {key}")
            out[key] = str(label or "").strip()[:80]
        return out


class DrilldownRowOut(BaseModel):
    label: str
    #: The row's stable id — for GA4 key events, the raw ``eventName`` (e.g. ``generate_lead``),
    #: kept alongside a custom ``label`` so the editor can key its per-event labels on it (#192).
    #: ``None`` for drill-downs that have no such id (top pages/queries/campaigns).
    key: str | None = None
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
    #: The one comparison every row on this grid used — the **org default**, never each client's
    #: own override (#312). A cross-client board sorted on numbers whose denominators differ per
    #: row is a ranking of nothing; the per-client setting governs that client's own dashboard,
    #: which is the screen it was chosen for. The grid names the period, so the difference is
    #: visible rather than assumed.
    compare: MarketingCompareWindow
    rows: list[OverviewRow] = Field(default_factory=list)
    total: int = 0


# --- My Day widget digest (#254) ------------------------------------------------------------- #
class MarketingSummaryRow(BaseModel):
    company_id: uuid.UUID
    company_name: str
    #: The headline KPI this row carries: ``sessions`` (GA4) where linked and visible, else
    #: ``clicks`` (GSC). One number per client — the widget is a teaser, not a grid.
    metric: str
    kpi: KpiValue


class MarketingSummary(BaseModel):
    range_days: int
    #: The comparison behind every row's delta — the org default, for the grid's reason above.
    compare: MarketingCompareWindow
    #: Linked clients in the caller's view. ``rows`` is capped, so the widget says
    #: "top n of this" instead of implying the cap is everything (docs/UX.md, no silent caps).
    linked_total: int = 0
    rows: list[MarketingSummaryRow] = Field(default_factory=list)


# --- the client picker on Marketing ---------------------------------------------------------- #
class MarketingClientSource(BaseModel):
    """One source a client has linked, and whether it is currently answering."""

    source: MarketingSource
    #: How many active links of this source the client has — two properties for two websites is
    #: ordinary, and a chip printed once would be describing half a connection.
    links: int = 1
    #: ``ok`` | ``pending`` (linked, nothing synced yet) | ``error`` (the last sync failed).
    #:
    #: Deliberately *not* :meth:`MarketingService._health`'s four-way answer: that one reads
    #: "disconnected" from the absence of a **Google** connection, which two of the five sources
    #: never have (an agency SE Ranking key, a per-website WordPress password). On a picker whose
    #: whole job is saying who is connected, that would print "niet verbonden" over a client
    #: whose numbers are arriving fine. What is left here is true of all five: a failing sync
    #: says so, and a link nothing has synced yet is pending.
    state: str = "ok"


class MarketingClientRow(BaseModel):
    company_id: uuid.UUID
    company_name: str
    #: Every source this client has linked, in the module's own display order.
    sources: list[MarketingClientSource] = Field(default_factory=list)


class MarketingClientList(BaseModel):
    rows: list[MarketingClientRow] = Field(default_factory=list)
    #: Linked clients in the caller's view. ``rows`` is capped, so the screen says "n of this"
    #: rather than letting the cap read as everything (docs/UX.md, no silent caps).
    total: int = 0
