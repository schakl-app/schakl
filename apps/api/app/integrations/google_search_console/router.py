"""REST endpoints for Google Search Console under ``/api/v1/google-search-console``.

Business-licensed — see LICENSE.

**The route list is the tool list** (CLAUDE.md §12). Every ``/api/v1`` operation becomes an MCP
tool generated from this app's own OpenAPI document, so this file is simultaneously the HTTP API
and the surface an agent sees at ``/mcp/google-search-console`` — which is why each handler is
named for the question somebody would ask rather than for the Google endpoint behind it.

**The property travels as a query parameter, never a path segment.** A Search Console
``siteUrl`` is ``sc-domain:klant.nl`` or ``https://www.klant.nl/`` — a value with a scheme and
slashes in it — and a path parameter is decoded before it is matched, so ``%2F`` becomes ``/``
and the route stops matching. Analytics puts its property id in the path because a property id
is a number; this surface takes ``?site=`` because a site is a URL. The generated tool carries
it as an ordinary argument either way.

**Every route is a GET, and every route declares a permission** (deny-by-default, §15). The
curated reads ride ``google_search_console.site.read``; the one that lets a caller compose their
own question rides ``google_search_console.report.run``, a separate grant for the reason
``google_analytics.report.run`` is one. All-GET is not an accident either: there is nothing here
worth writing, and a read must keep answering past a licence expiry (§18).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.google_search_console.client import HOURLY_DAYS
from app.integrations.google_search_console.schemas import (
    GoogleSearchConsoleAiVisibility,
    GoogleSearchConsoleInspection,
    GoogleSearchConsoleMovers,
    GoogleSearchConsoleOverview,
    GoogleSearchConsoleReport,
    GoogleSearchConsoleSearchTypes,
    GoogleSearchConsoleSite,
    GoogleSearchConsoleSiteList,
    GoogleSearchConsoleSitemap,
    GoogleSearchConsoleSitemapList,
)
from app.integrations.google_search_console.service import (
    DEFAULT_MIN_IMPRESSIONS,
    DEFAULT_ROWS,
    MAX_ROWS,
    GoogleSearchConsoleService,
)

router = APIRouter(prefix="/google-search-console", tags=["google-search-console"])

_READ = "google_search_console.site.read"
_RUN = "google_search_console.report.run"

_SITE = Query(
    ...,
    description=(
        "The property exactly as GET /sites lists it: sc-domain:klant.nl for a domain "
        "property or https://www.klant.nl/ (trailing slash included) for a URL-prefix "
        "property. A bare hostname is read as the domain property."
    ),
)

_PERIOD = Query(
    None,
    description=(
        "The span to report on: a trailing window (30d, 90d, 365d), a preset (month, "
        "last_month, quarter, last_quarter) or a named calendar period (2026-07, 2026-Q3). "
        "An unknown value falls back to 30d. Search Console keeps its days in Pacific time; "
        "the last two or three days are still being collected and are flagged as fresh."
    ),
)

_SEARCH_TYPE = Query(
    None,
    description=(
        "web (default; includes AI Overviews, which cannot be split out), image, video, news, "
        "discover or googleNews."
    ),
)

_COMPARE = Query(None, description="year (default — what seasonality survives) or previous.")

_FILTERS = Query(
    default_factory=list,
    description=(
        "Dimension filters, repeatable: dimension==value, dimension!=value, dimension=@value "
        "(contains), dimension!@value, dimension=~regex, dimension!~regex, on query, page, "
        "country (ISO 3166-1 alpha-3, lower case: nld), device (DESKTOP/MOBILE/TABLET) or "
        "searchAppearance. All clauses must match. A clause that parses as none of these is "
        "refused rather than ignored."
    ),
)


# --- what exists ----------------------------------------------------------------------------- #
@router.get(
    "/sites",
    response_model=GoogleSearchConsoleSiteList,
    dependencies=[require_permission(_READ)],
)
async def list_google_search_console_sites(
    query: str | None = Query(None, description="Filter by site URL."),
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleSiteList:
    """Every Search Console property the signed-in user's Google account can read. Start here.

    Answers rather than refuses when there is no credential: `connected` false means nobody has
    connected Google, `has_scope` false means the grant does not carry Search Console — different
    states with different cures, and neither is an error about this request. A property with
    permission_level SITE_UNVERIFIED_USER answers no data until somebody verifies it.
    """
    return await GoogleSearchConsoleService(ctx).sites(query)


@router.get(
    "/site",
    response_model=GoogleSearchConsoleSite,
    dependencies=[require_permission(_READ)],
)
async def get_google_search_console_site(
    site: str = _SITE,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleSite:
    """One property's record: its kind (domain or URL prefix) and what this account may do there."""
    return await GoogleSearchConsoleService(ctx).site_detail(site)


@router.get(
    "/sitemaps",
    response_model=GoogleSearchConsoleSitemapList,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_sitemaps(
    site: str = _SITE,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleSitemapList:
    """The sitemaps submitted for a property: when Google last read each, how many URLs it
    declares, and how many errors and warnings it carries. A sitemap nobody submitted, or one
    with errors, is the usual answer to "why are the new pages not indexed"."""
    return await GoogleSearchConsoleService(ctx).sitemaps(site)


@router.get(
    "/sitemap",
    response_model=GoogleSearchConsoleSitemap,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_sitemap(
    site: str = _SITE,
    feedpath: str = Query(..., description="The sitemap's own URL, as /sitemaps lists it."),
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleSitemap:
    """One sitemap by its URL."""
    return await GoogleSearchConsoleService(ctx).sitemap(site, feedpath)


# --- what happened --------------------------------------------------------------------------- #
@router.get(
    "/overview",
    response_model=GoogleSearchConsoleOverview,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_overview(
    site: str = _SITE,
    period: str | None = _PERIOD,
    compare: str | None = _COMPARE,
    search_type: str | None = _SEARCH_TYPE,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleOverview:
    """How a property did over a period — clicks, impressions, CTR and average position —
    against the same period a year earlier, with the change already computed, plus the split
    by device.

    ctr is a fraction (0.0432 is 4,32 %). position is an average and lower is better, which
    the change says for itself. A null relative change means there was no baseline, which is
    not the same as no change. fresh_from names the first day Google is still collecting.
    """
    return await GoogleSearchConsoleService(ctx).overview(
        site, period=period, compare=compare, search_type=search_type
    )


@router.get(
    "/search-types",
    response_model=GoogleSearchConsoleSearchTypes,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_search_types(
    site: str = _SITE,
    period: str | None = _PERIOD,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleSearchTypes:
    """The four metrics per Google surface — web, image, video, news, Discover, Google News —
    so "where is this site actually seen" is one answer. AI Overviews sit inside web and the
    API cannot split them out; see /ai-visibility for what it can say."""
    return await GoogleSearchConsoleService(ctx).search_types(site, period=period)


@router.get(
    "/timeseries",
    response_model=GoogleSearchConsoleReport,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_timeseries(
    site: str = _SITE,
    period: str | None = _PERIOD,
    search_type: str | None = _SEARCH_TYPE,
    filters: list[str] = _FILTERS,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleReport:
    """The four metrics day by day, oldest first. A day with nothing shown is omitted by
    Google rather than answered as zero."""
    return await GoogleSearchConsoleService(ctx).timeseries(
        site, period=period, search_type=search_type, filters=list(filters)
    )


@router.get(
    "/breakdown",
    response_model=GoogleSearchConsoleReport,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_breakdown(
    site: str = _SITE,
    dimension: str = Query(
        "query",
        description="query (default), page, country, device or searchAppearance.",
    ),
    period: str | None = _PERIOD,
    search_type: str | None = _SEARCH_TYPE,
    filters: list[str] = _FILTERS,
    limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
    offset: int = Query(0, ge=0),
    order: str | None = Query(
        None,
        description=(
            "Google ranks by clicks. Another metric (-impressions, -ctr, +position) is applied "
            "over the first thousand clicks-ranked rows, and the answer says so in warnings."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleReport:
    """One dimension, ranked — top queries, top pages, countries, devices, or the search
    appearances (rich results, video, product snippets…) the site was shown with.

    truncated says the answer is a page of a longer list, so a top-25 is never mistaken for
    the whole. Filter with query=@brand to separate branded from unbranded.
    """
    return await GoogleSearchConsoleService(ctx).breakdown(
        site,
        dimension=dimension,
        period=period,
        search_type=search_type,
        filters=list(filters),
        limit=limit,
        offset=offset,
        order=order,
    )


@router.get(
    "/hourly",
    response_model=GoogleSearchConsoleReport,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_hourly(
    site: str = _SITE,
    days: int = Query(
        2,
        ge=1,
        le=HOURLY_DAYS,
        description="How many days back, today included. Google keeps ten days of hourly rows.",
    ),
    search_type: str | None = _SEARCH_TYPE,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleReport:
    """Hour by hour over the last few days, today included and still moving — for "what
    happened this morning" and "did the launch land". Every row may still change; there is no
    final hourly data, by Google's design."""
    return await GoogleSearchConsoleService(ctx).hourly(site, days=days, search_type=search_type)


@router.get(
    "/movers",
    response_model=GoogleSearchConsoleMovers,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_movers(
    site: str = _SITE,
    period: str | None = _PERIOD,
    compare: str | None = _COMPARE,
    dimension: str = Query("query", description="query (default) or page."),
    limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
    min_impressions: float = Query(
        DEFAULT_MIN_IMPRESSIONS,
        ge=0,
        description="Ignore anything shown fewer times than this in either span.",
    ),
    search_type: str | None = _SEARCH_TYPE,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleMovers:
    """Which queries or pages moved most in average position between this period and the one
    it is compared against. change is positive for a climb. entered and dropped count what
    appeared or vanished entirely, which the rows cannot show."""
    return await GoogleSearchConsoleService(ctx).movers(
        site,
        period=period,
        compare=compare,
        dimension=dimension,
        limit=limit,
        min_impressions=min_impressions,
        search_type=search_type,
    )


# --- the index ------------------------------------------------------------------------------- #
@router.get(
    "/inspect",
    response_model=GoogleSearchConsoleInspection,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_inspect_url(
    site: str = _SITE,
    url: str = Query(..., description="The page to inspect. Must be under the property."),
    language: str | None = Query(None, description="BCP-47 code for the issue texts, e.g. nl."),
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleInspection:
    """What Google's index holds for one URL: whether it is indexed (verdict PASS) and if not
    why (coverage_state, indexing_state, robots_txt_state, page_fetch_state), which canonical
    Google chose against the one the page declares, when it was last crawled, and the rich
    results found. One URL per call — Google allows 2 000 inspections a day per property."""
    return await GoogleSearchConsoleService(ctx).inspect(site, url=url, language=language)


# --- generative AI --------------------------------------------------------------------------- #
@router.get(
    "/ai-visibility",
    response_model=GoogleSearchConsoleAiVisibility,
    dependencies=[require_permission(_READ)],
)
async def google_search_console_ai_visibility(
    site: str = _SITE,
    period: str | None = _PERIOD,
    compare: str | None = _COMPARE,
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleAiVisibility:
    """How visible the site is in Google's generative AI features — AI Overviews and AI Mode —
    as far as the Search Console API can say.

    Read available first. Search Console has shown a Generative AI performance report since
    June 2026 (impressions by page, country, device and date), and as of the API revision in
    api_revision_checked the Search Analytics API has no search type for it: available is
    false, reason names the state, and report_url opens the report in the console, which is
    where those numbers live. Never estimate the missing figures from web totals — AI
    Overviews are folded into web and cannot be separated. When Google adds the search type,
    sources carries the same overview shape per generative feature.
    """
    return await GoogleSearchConsoleService(ctx).ai_visibility(
        site, period=period, compare=compare
    )


# --- ask your own question --------------------------------------------------------------------#
@router.get(
    "/query",
    response_model=GoogleSearchConsoleReport,
    dependencies=[require_permission(_RUN)],
)
async def google_search_console_query(
    site: str = _SITE,
    dimensions: list[str] = Query(
        default_factory=list,
        description=(
            "Repeatable, in row-key order: query, page, country, device, searchAppearance, "
            "date, hour. None is a total. hour forces data_state hourly_all."
        ),
    ),
    period: str | None = _PERIOD,
    search_type: str | None = _SEARCH_TYPE,
    filters: list[str] = _FILTERS,
    aggregation: str | None = Query(
        None, description="auto (default), byProperty, byPage or byNewsShowcasePanel."
    ),
    data_state: str | None = Query(
        None,
        description=(
            "all (default here: includes the fresh, still-changing days), final (Google's "
            "default: finalised data only, so the last days are missing) or hourly_all."
        ),
    ),
    limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> GoogleSearchConsoleReport:
    """Any dimensions crossed, with any filters, any aggregation and any data state — the
    escape hatch for questions the curated reads do not answer (query by page, page by
    country, branded queries by device…).

    Read-only by construction: the Search Analytics API has no write verb, and the site is a
    value this connection can already list. An unknown dimension, search type or filter
    field is refused here with the list of ones that work, before Google is asked.
    """
    return await GoogleSearchConsoleService(ctx).query(
        site,
        dimensions=list(dimensions),
        period=period,
        search_type=search_type,
        filters=list(filters),
        aggregation=aggregation,
        data_state=data_state,
        limit=limit,
        offset=offset,
    )
