"""Request/response shapes for the Search Console surface. Business-licensed — see LICENSE.

Three decisions worth stating.

**A row is ``dimensions`` + ``metrics``, never Google's positional ``keys``.** A Search Analytics
row carries its group-by values as a list in the order the request named them, which is a shape
that is right until somebody reorders the request. The four metrics are the same on every row
of every query, so a reader never has to know which position ``country`` was.

**A sitemap and an inspection are Google's records, flattened but not re-modelled.** The fields
an agency acts on (the verdict, the canonical, the fetch state, the errors count) are named; the
long tails (referring URLs, rich-result items, AMP issues) travel as lists of Google's own JSON,
because a field Google adds next quarter is a field this module would otherwise drop.

**The AI-visibility answer has an ``available`` flag and is never an error.** The Generative AI
report is real, the numbers in it are real, and the API does not expose them today. That is a
state with a cure — open the report — and a state is something a caller reads, not something a
route refuses (#411: a credential's absence is evidence, never a verdict on the screen).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class GoogleSearchConsoleSite(BaseModel):
    """One property the connected Google account can read."""

    #: The exact ``siteUrl`` every other call takes: ``sc-domain:klant.nl`` or ``https://…/``.
    site_url: str
    #: What a list prints: the domain without its ``sc-domain:`` prefix.
    display_name: str
    #: ``domain`` (every subdomain and protocol) or ``url_prefix`` (one origin, one path).
    site_type: str = ""
    #: Google's own word for what this account may do here. ``SITE_UNVERIFIED_USER`` is a
    #: property somebody added and never verified — it answers no data.
    permission_level: str = ""
    console_url: str = ""


class GoogleSearchConsoleSiteList(BaseModel):
    """The properties this caller's Google connection reaches — or why it reaches none.

    ``connected`` / ``has_scope`` are reported rather than raised (#411): "connect Google" and
    "allow Search Console" are different acts, and only the payload can say which is missing.
    """

    connected: bool = False
    has_scope: bool = False
    sites: list[GoogleSearchConsoleSite] = Field(default_factory=list)
    error: str | None = None
    #: The flag the Google connect flow needs to add ``webmasters.readonly`` to this grant.
    connect_flag: str = "include_search_console"


class GoogleSearchConsoleSitemap(BaseModel):
    """One submitted sitemap, as Google last saw it."""

    path: str
    #: ``SITEMAP``, ``URL_LIST``, ``RSS_FEED``, ``ATOM_FEED``, ``PATTERN_SITEMAP``… or
    #: ``NOT_SITEMAP`` — the file at that URL is not one.
    type: str = ""
    last_submitted: str | None = None
    last_downloaded: str | None = None
    is_pending: bool = False
    is_sitemaps_index: bool = False
    errors: int = 0
    warnings: int = 0
    #: ``{type, submitted}`` per content type — how many URLs the file declares.
    contents: list[dict[str, Any]] = Field(default_factory=list)


class GoogleSearchConsoleSitemapList(BaseModel):
    site_url: str
    sitemaps: list[GoogleSearchConsoleSitemap] = Field(default_factory=list)


class GoogleSearchConsolePeriod(BaseModel):
    date_from: date
    date_to: date
    days: int


class GoogleSearchConsoleCompare(BaseModel):
    date_from: date
    date_to: date
    mode: str


class GoogleSearchConsoleRow(BaseModel):
    dimensions: dict[str, str] = Field(default_factory=dict)
    #: Always the four: ``clicks``, ``impressions``, ``ctr`` (a fraction) and ``position``.
    metrics: dict[str, float] = Field(default_factory=dict)


class GoogleSearchConsoleReport(BaseModel):
    """A Search Analytics query, flattened."""

    site_url: str
    period: GoogleSearchConsolePeriod | None = None
    search_type: str = "web"
    data_state: str = "all"
    dimensions: list[str] = Field(default_factory=list)
    rows: list[GoogleSearchConsoleRow] = Field(default_factory=list)
    row_count: int = 0
    #: The answer is a page of a longer list. Google reports no total, so this is learned by
    #: asking for one row more than is kept (§17's rule) — a prefix presented as a whole is the
    #: worst answer available, because it looks like it worked.
    truncated: bool = False
    #: The first day Google is still collecting: everything from here on may still change.
    #: Search Console finalises two to three days late, and a number that will move tomorrow
    #: should not be read as one that will not.
    fresh_from: date | None = None
    warnings: list[str] = Field(default_factory=list)


class GoogleSearchConsoleChange(BaseModel):
    value_from: float
    value_to: float
    absolute: float
    #: ``None`` when the baseline is zero: a percentage against nothing is undefined, and a
    #: model handed a number writes a sentence about it.
    relative: float | None = None
    #: ``position`` is the one metric where a smaller number is the better one.
    lower_is_better: bool = False


class GoogleSearchConsoleOverview(BaseModel):
    """How a property did over a period, against what, and by how much."""

    site_url: str
    period: GoogleSearchConsolePeriod
    compared_with: GoogleSearchConsoleCompare
    search_type: str = "web"
    totals: dict[str, float] = Field(default_factory=dict)
    previous_totals: dict[str, float] = Field(default_factory=dict)
    change: dict[str, GoogleSearchConsoleChange | None] = Field(default_factory=dict)
    #: The four metrics per device — ``DESKTOP`` / ``MOBILE`` / ``TABLET``.
    devices: dict[str, dict[str, float]] = Field(default_factory=dict)
    fresh_from: date | None = None
    warnings: list[str] = Field(default_factory=list)


class GoogleSearchConsoleSearchTypes(BaseModel):
    """The four metrics per search type — where on Google the site was actually seen."""

    site_url: str
    period: GoogleSearchConsolePeriod
    by_type: dict[str, dict[str, float]] = Field(default_factory=dict)
    fresh_from: date | None = None


class GoogleSearchConsoleMover(BaseModel):
    label: str
    position: float
    previous_position: float
    #: Positive is a climb (a smaller position number), matching every ranking table here.
    change: float
    clicks: float
    impressions: float


class GoogleSearchConsoleMovers(BaseModel):
    """Which queries (or pages) moved most between this period and the one before it."""

    site_url: str
    period: GoogleSearchConsolePeriod
    compared_with: GoogleSearchConsoleCompare
    dimension: str = "query"
    min_impressions: float = 0
    rows: list[GoogleSearchConsoleMover] = Field(default_factory=list)
    #: Present now with no counterpart before, and the reverse. A term that has *dropped out*
    #: entirely is news the table above cannot carry, so at least the count of them is stated.
    entered: int = 0
    dropped: int = 0


class GoogleSearchConsoleInspection(BaseModel):
    """What Google's index holds for one URL — the URL Inspection tool, as data."""

    site_url: str
    inspected_url: str
    #: ``PASS`` (indexed), ``PARTIAL``, ``FAIL`` (not indexed), ``NEUTRAL`` (excluded on purpose).
    verdict: str = ""
    #: Google's own sentence about coverage, e.g. "Submitted and indexed".
    coverage_state: str = ""
    indexing_state: str = ""
    robots_txt_state: str = ""
    page_fetch_state: str = ""
    crawled_as: str = ""
    last_crawl_time: str | None = None
    #: The URL Google chose as canonical, absent when the page is not indexed; and the one the
    #: page itself declares. A pair that disagrees is the usual "why is my page not ranking".
    google_canonical: str | None = None
    user_canonical: str | None = None
    referring_urls: list[str] = Field(default_factory=list)
    sitemaps: list[str] = Field(default_factory=list)
    #: Google's own JSON for the three analyses, absent where Google found nothing to say.
    rich_results: dict[str, Any] | None = None
    mobile_usability: dict[str, Any] | None = None
    amp: dict[str, Any] | None = None
    #: The same inspection in the console, for a person.
    inspection_link: str = ""


class GoogleSearchConsoleAiSource(BaseModel):
    """One generative AI search type's numbers, for the day the API answers them."""

    totals: dict[str, float] = Field(default_factory=dict)
    previous_totals: dict[str, float] = Field(default_factory=dict)
    change: dict[str, GoogleSearchConsoleChange | None] = Field(default_factory=dict)
    top_pages: list[GoogleSearchConsoleRow] = Field(default_factory=list)


class GoogleSearchConsoleAiVisibility(BaseModel):
    """How visible the site is in Google's generative AI features — and whether the API can say.

    ``available`` is the whole answer today (see ``client.GENERATIVE_AI_SEARCH_TYPES``): the
    report exists in the console, the numbers exist, and the Search Analytics API does not
    return them. ``report_url`` is where they are. When Google adds the search type, ``sources``
    fills in and nothing else about this shape changes.
    """

    site_url: str
    available: bool = False
    #: An i18n key naming the state, never prose: the screen and the model both read it.
    reason: str | None = None
    #: The generative AI features the console reports on today.
    features: list[str] = Field(default_factory=lambda: ["AI Overviews", "AI Mode"])
    #: The Generative AI performance report for this property, in the console.
    report_url: str = ""
    #: The discovery-document revision the vocabulary was last checked against.
    api_revision_checked: str = ""
    period: GoogleSearchConsolePeriod | None = None
    compared_with: GoogleSearchConsoleCompare | None = None
    sources: dict[str, GoogleSearchConsoleAiSource] = Field(default_factory=dict)
