"""REST endpoints for Google Analytics under ``/api/v1/google-analytics``.

Business-licensed — see LICENSE.

**The route list is the tool list** (CLAUDE.md §12). Every ``/api/v1`` operation becomes an MCP
tool generated from this app's own OpenAPI document, so this file is simultaneously the HTTP API
and the surface an agent sees at ``/mcp/google-analytics`` — which is why each handler is named
for the question somebody would ask rather than for the Google endpoint behind it, and why the
listings are seven routes rather than one route with a ``kind`` parameter: a tool called
``google_analytics_key_events`` is discoverable and ``google_analytics_resources(kind=…)`` is a
tool whose vocabulary lives in a docstring.

**Every route is a GET, and every route declares a permission** (deny-by-default, §15). The
curated reads ride ``google_analytics.property.read``; the three that let a caller compose their
own question ride ``google_analytics.report.run``, which is a separate grant for the reason
``google_ads.query.run`` is one. All-GET is not an accident either: there is nothing here worth
writing, and a read must keep answering past a licence expiry (§18).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.google_analytics.schemas import (
    GoogleAnalyticsCompatibility,
    GoogleAnalyticsMetadata,
    GoogleAnalyticsOverview,
    GoogleAnalyticsProperty,
    GoogleAnalyticsPropertyList,
    GoogleAnalyticsRealtime,
    GoogleAnalyticsReport,
    GoogleAnalyticsResourceList,
)
from app.integrations.google_analytics.service import (
    DEFAULT_ROWS,
    MAX_REALTIME_ROWS,
    MAX_ROWS,
    GoogleAnalyticsService,
)

router = APIRouter(prefix="/google-analytics", tags=["google-analytics"])

_READ = "google_analytics.property.read"
_RUN = "google_analytics.report.run"

_PERIOD = Query(
    None,
    description=(
        "The span to report on: a trailing window (30d, 90d, 365d), a preset (month, "
        "last_month, quarter, last_quarter) or a named calendar period (2026-07, 2026-Q3). "
        "An unknown value falls back to 30d. Dates are days in the property's own reporting "
        "timezone — GET /properties/{property_id} tells you which."
    ),
)

_FILTERS = Query(
    default_factory=list,
    description=(
        "Dimension filters, repeatable: name==value (exact), name=@value (contains) or "
        "name=^value (begins with). All clauses must match. A clause that parses as none of "
        "these is refused rather than ignored."
    ),
)


# --- what exists ----------------------------------------------------------------------------- #
@router.get(
    "/properties",
    response_model=GoogleAnalyticsPropertyList,
    dependencies=[require_permission(_READ)],
)
async def list_google_analytics_properties(
    query: str | None = Query(None, description="Filter by property name or id."),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsPropertyList:
    """Every GA4 property the signed-in user's Google account can read. Start here.

    Answers rather than refuses when there is no credential: `connected` false means nobody has
    connected Google, `has_scope` false means the grant does not carry Analytics — different
    states with different cures, and neither is an error about this request.
    """
    return await GoogleAnalyticsService(ctx).properties(query)


@router.get(
    "/properties/{property_id}",
    response_model=GoogleAnalyticsProperty,
    dependencies=[require_permission(_READ)],
)
async def get_google_analytics_property(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsProperty:
    """One property's own record: its currency, industry and **reporting timezone**.

    Read the timezone before comparing these numbers with anything computed elsewhere: every
    date in every report below is a day in that zone, not in the workspace's.
    """
    return await GoogleAnalyticsService(ctx).property_detail(property_id)


@router.get(
    "/properties/{property_id}/data-streams",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_data_streams(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """The web and app streams feeding this property, with their measurement IDs.

    The measurement ID is what a Tag Manager container has to be sending to; a property with no
    stream is a property nothing has ever been measured into.
    """
    return await GoogleAnalyticsService(ctx).resources(property_id, "data-streams")


@router.get(
    "/properties/{property_id}/key-events",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_key_events(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """The events this property counts as conversions (GA4's own name for them is key events).

    What a client is actually promised is measured. An empty list on a property whose report
    quotes conversions means the number is counting nothing.
    """
    return await GoogleAnalyticsService(ctx).resources(property_id, "key-events")


@router.get(
    "/properties/{property_id}/custom-dimensions",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_custom_dimensions(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """The property's own dimensions — the fields a report about *this* client can group by."""
    return await GoogleAnalyticsService(ctx).resources(property_id, "custom-dimensions")


@router.get(
    "/properties/{property_id}/custom-metrics",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_custom_metrics(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """The property's own metrics, with the unit each one is measured in."""
    return await GoogleAnalyticsService(ctx).resources(property_id, "custom-metrics")


@router.get(
    "/properties/{property_id}/google-ads-links",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_google_ads_links(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """Which Google Ads customers this property is linked to.

    The answer to "why does Analytics show no paid traffic": an unlinked property cannot
    attribute it, however much the campaign spent.
    """
    return await GoogleAnalyticsService(ctx).resources(property_id, "google-ads-links")


@router.get(
    "/properties/{property_id}/firebase-links",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_firebase_links(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """Which Firebase projects feed this property — the app half of a client's measurement."""
    return await GoogleAnalyticsService(ctx).resources(property_id, "firebase-links")


@router.get(
    "/properties/{property_id}/data-retention",
    response_model=GoogleAnalyticsResourceList,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_data_retention(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsResourceList:
    """How long this property keeps event-level data.

    Worth asking before reporting on a long window: a property set to two months has not lost
    last year's data, it deleted it — and an empty chart cannot tell you which.
    """
    return await GoogleAnalyticsService(ctx).data_retention(property_id)


@router.get(
    "/properties/{property_id}/metadata",
    response_model=GoogleAnalyticsMetadata,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_metadata(
    property_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsMetadata:
    """Every dimension and metric this property will accept, custom ones included.

    Read this before composing a report: GA4 refuses an unknown field with a 400 that names
    neither what was wrong nor what would have worked.
    """
    return await GoogleAnalyticsService(ctx).metadata(property_id)


# --- what happened --------------------------------------------------------------------------- #
@router.get(
    "/properties/{property_id}/overview",
    response_model=GoogleAnalyticsOverview,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_overview(
    property_id: str,
    period: str | None = _PERIOD,
    compare: str | None = Query(
        None, description="year (default — what seasonality survives) or previous."
    ),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsOverview:
    """How a property did over a period, against the same period a year earlier, with the change
    already computed — plus the acquisition channel split.

    Rates (engagementRate, bounceRate) are fractions: 0.4595 is 45,95 %. A null relative change
    means there was no baseline, which is not the same as no change. `warnings` says when GA4
    sampled or withheld part of the answer.
    """
    return await GoogleAnalyticsService(ctx).overview(
        property_id, period=period, compare=compare
    )


@router.get(
    "/properties/{property_id}/timeseries",
    response_model=GoogleAnalyticsReport,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_timeseries(
    property_id: str,
    metrics: list[str] = Query(
        default_factory=list,
        description="Repeatable GA4 metric names; defaults to sessions, totalUsers, keyEvents.",
    ),
    period: str | None = _PERIOD,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsReport:
    """The chosen metrics day by day, oldest first."""
    return await GoogleAnalyticsService(ctx).timeseries(
        property_id, metrics=list(metrics), period=period
    )


@router.get(
    "/properties/{property_id}/breakdown",
    response_model=GoogleAnalyticsReport,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_breakdown(
    property_id: str,
    dimension: str = Query(
        ...,
        description=(
            "One GA4 dimension: pagePath, landingPage, sessionDefaultChannelGroup, "
            "sessionSource, sessionMedium, deviceCategory, country, city, eventName, "
            "browser, or any custom dimension from /metadata."
        ),
    ),
    metrics: list[str] = Query(
        default_factory=list,
        description="Repeatable; defaults to sessions, totalUsers, keyEvents.",
    ),
    period: str | None = _PERIOD,
    limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
    order: str | None = Query(
        None, description="A named metric or dimension; prefix with - for descending."
    ),
    filters: list[str] = _FILTERS,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsReport:
    """One dimension, ranked — top pages, channels, sources, devices, countries, events.

    `row_count` is how many rows exist and `truncated` says the answer is a page of them, so a
    top-10 is never mistaken for the whole list.
    """
    return await GoogleAnalyticsService(ctx).breakdown(
        property_id,
        dimension=dimension,
        metrics=list(metrics),
        period=period,
        limit=limit,
        order=order,
        filters=list(filters),
    )


@router.get(
    "/properties/{property_id}/realtime",
    response_model=GoogleAnalyticsRealtime,
    dependencies=[require_permission(_READ)],
)
async def google_analytics_realtime(
    property_id: str,
    dimensions: list[str] = Query(
        default_factory=list,
        description="Repeatable realtime dimensions: unifiedScreenName, country, deviceCategory.",
    ),
    metrics: list[str] = Query(
        default_factory=list, description="Repeatable; defaults to activeUsers."
    ),
    limit: int = Query(MAX_REALTIME_ROWS, ge=1, le=MAX_REALTIME_ROWS),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsRealtime:
    """Who is on the site in the last thirty minutes. There is no period here, by design —
    realtime answers about now and about nothing else."""
    return await GoogleAnalyticsService(ctx).realtime(
        property_id, dimensions=list(dimensions), metrics=list(metrics), limit=limit
    )


# --- ask your own question --------------------------------------------------------------------#
@router.get(
    "/properties/{property_id}/report",
    response_model=GoogleAnalyticsReport,
    dependencies=[require_permission(_RUN)],
)
async def google_analytics_report(
    property_id: str,
    metrics: list[str] = Query(..., description="Repeatable GA4 metric names. At least one."),
    dimensions: list[str] = Query(
        default_factory=list, description="Repeatable GA4 dimension names; none is a total."
    ),
    period: str | None = _PERIOD,
    limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
    offset: int = Query(0, ge=0),
    order: str | None = Query(
        None, description="A named metric or dimension; prefix with - for descending."
    ),
    filters: list[str] = _FILTERS,
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsReport:
    """Any dimensions crossed with any metrics — the escape hatch for questions the curated
    reads do not answer.

    Read-only by construction: the Data API has no write verb, and the property is taken from
    the path, so a report can never reach a property this connection could not already list.
    Check /metadata first — GA4 refuses an unknown or incompatible field with a 400 that names
    neither half, which this returns as a 422 carrying Google's own reason code.
    """
    return await GoogleAnalyticsService(ctx).report(
        property_id,
        dimensions=list(dimensions),
        metrics=list(metrics),
        period=period,
        limit=limit,
        offset=offset,
        order=order,
        filters=list(filters),
    )


@router.get(
    "/properties/{property_id}/pivot",
    response_model=GoogleAnalyticsReport,
    dependencies=[require_permission(_RUN)],
)
async def google_analytics_pivot(
    property_id: str,
    metrics: list[str] = Query(..., description="Repeatable GA4 metric names. At least one."),
    pivot_on: str = Query(..., description="The dimension to spread across the columns."),
    dimensions: list[str] = Query(
        default_factory=list, description="The dimension(s) that make the rows."
    ),
    period: str | None = _PERIOD,
    limit: int = Query(DEFAULT_ROWS, ge=1, le=MAX_ROWS),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsReport:
    """One dimension crossed against another: channel by month, device by landing page."""
    return await GoogleAnalyticsService(ctx).pivot(
        property_id,
        dimensions=list(dimensions),
        metrics=list(metrics),
        pivot_on=pivot_on,
        period=period,
        limit=limit,
    )


@router.get(
    "/properties/{property_id}/compatibility",
    response_model=GoogleAnalyticsCompatibility,
    dependencies=[require_permission(_RUN)],
)
async def google_analytics_compatibility(
    property_id: str,
    dimensions: list[str] = Query(default_factory=list, description="Already-chosen dimensions."),
    metrics: list[str] = Query(default_factory=list, description="Already-chosen metrics."),
    ctx: RequestContext = Depends(require_context),
) -> GoogleAnalyticsCompatibility:
    """Which fields may still be combined with the ones named.

    GA4 refuses some pairs outright and its refusal identifies neither half, so this is how a
    working report is composed rather than guessed at through repeated 400s.
    """
    return await GoogleAnalyticsService(ctx).compatibility(
        property_id, list(dimensions), list(metrics)
    )
