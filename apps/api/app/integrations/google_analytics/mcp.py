"""Curated AI tools for Google Analytics. Business-licensed — see LICENSE.

**These reach the in-app assistant, and the MCP server gets the routes instead.** ``mcp_tools``
is read by ``app/core/ai/tools.py`` alone; ``/mcp`` is built by ``FastMCP.from_fastapi(...)``
off the OpenAPI document, so an external client gets the seventeen operations in ``router.py``
at ``/mcp/google-analytics`` and none of the six below. Both catalogs are real and neither is
the other's summary — the same arrangement ``google_ads`` documents, for the same reason.

What earns a place here is a **richer shape than a 1:1 endpoint mapping**, and two of the six do
work no single route does:

* ``google_analytics.overview`` folds the period, the compared period, the change and the channel
  split into one answer, so the model is never doing arithmetic it will do differently next time.
* ``google_analytics.setup`` answers *"is this client's measurement actually working?"* — key
  events, data streams, the Ads link and the retention window in one call. That is four routes
  and a judgement, and the judgement is the part a model gets wrong when it has to remember to
  make the fourth call.

Every handler runs under the caller's own :class:`RequestContext` and its spec names the
permission the service is about to demand, so a caller holding neither key is never told these
tools exist (§15). Read-only, all six — there is nothing in a client's Analytics property this
platform has any business writing.
"""

from __future__ import annotations

from typing import Any

from app.core.ai import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.integrations.google_analytics.service import GoogleAnalyticsService

_READ = "google_analytics.property.read"
_RUN = "google_analytics.report.run"

_PERIOD_DESCRIPTION = (
    "30d, 90d, 365d, month, last_month, quarter, last_quarter, or a named period like "
    "2026-07 or 2026-Q3. Dates are days in the property's own reporting timezone."
)


def _property_arg(args: dict[str, Any]) -> str:
    return str(args.get("property_id") or "").strip()


async def _properties(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    listing = await GoogleAnalyticsService(ctx).properties(args.get("query"))
    if not listing.connected or not listing.has_scope:
        # A state, not an error: the caller can act on exactly one of these two and the
        # difference is which button they press (#411).
        return ToolResult(
            data={
                "connected": listing.connected,
                "has_analytics_access": listing.has_scope,
                "error": (
                    "errors.google_not_connected"
                    if not listing.connected
                    else "errors.google_analytics_scope_missing"
                ),
                "properties": [],
            }
        )
    rows = listing.properties[:50]
    return ToolResult(
        data={
            "properties": [row.model_dump(mode="json") for row in rows],
            "shown": len(rows),
            "total": len(listing.properties),
            # A short list reads as "we are not in that account" unless it says otherwise.
            "truncated": listing.truncated or len(rows) < len(listing.properties),
        },
        sources=tuple(
            Source(type="ga4_property", id=row.property_id, label=row.display_name)
            for row in rows
        ),
    )


async def _overview(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    result = await GoogleAnalyticsService(ctx).overview(
        _property_arg(args),
        period=str(args.get("period")) if args.get("period") else None,
        compare=str(args.get("compare")) if args.get("compare") else None,
    )
    return ToolResult(
        data=result.model_dump(mode="json"),
        sources=(Source(type="ga4_property", id=result.property_id, label=""),),
    )


async def _breakdown(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    metrics = args.get("metrics")
    result = await GoogleAnalyticsService(ctx).breakdown(
        _property_arg(args),
        dimension=str(args.get("dimension") or "sessionDefaultChannelGroup"),
        metrics=[str(m) for m in metrics] if isinstance(metrics, list) else None,
        period=str(args.get("period")) if args.get("period") else None,
        limit=_limit(args.get("limit"), 10),
    )
    return ToolResult(data=result.model_dump(mode="json"))


async def _timeseries(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    metrics = args.get("metrics")
    result = await GoogleAnalyticsService(ctx).timeseries(
        _property_arg(args),
        metrics=[str(m) for m in metrics] if isinstance(metrics, list) else None,
        period=str(args.get("period")) if args.get("period") else None,
    )
    return ToolResult(data=result.model_dump(mode="json"))


async def _setup(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """Is this property actually measuring anything, and is what it measures what was promised?

    Four reads folded, and the fold is the tool: a model asked "is the tracking working" that
    has to remember to make a fourth call will sometimes not, and the answer it gives after
    three is confidently wrong rather than incomplete.
    """
    property_id = _property_arg(args)
    service = GoogleAnalyticsService(ctx)
    detail = await service.property_detail(property_id)
    streams = await service.resources(property_id, "data-streams")
    key_events = await service.resources(property_id, "key-events")
    ads_links = await service.resources(property_id, "google-ads-links")
    retention = await service.data_retention(property_id)
    return ToolResult(
        data={
            "property": detail.model_dump(mode="json"),
            "data_streams": streams.rows,
            "key_events": key_events.rows,
            "google_ads_links": ads_links.rows,
            "data_retention": retention.rows[0] if retention.rows else None,
            # Stated rather than left to be inferred from three empty lists, because each of
            # these is a different conversation with a different person.
            "findings": {
                "no_data_stream": not streams.rows,
                "no_key_events": not key_events.rows,
                "no_google_ads_link": not ads_links.rows,
            },
        },
        sources=(
            Source(type="ga4_property", id=detail.property_id, label=detail.display_name),
        ),
    )


async def _realtime(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    dimensions = args.get("dimensions")
    result = await GoogleAnalyticsService(ctx).realtime(
        _property_arg(args),
        dimensions=[str(d) for d in dimensions] if isinstance(dimensions, list) else None,
        limit=_limit(args.get("limit"), 20),
    )
    return ToolResult(data=result.model_dump(mode="json"))


async def _report(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    metrics = args.get("metrics")
    dimensions = args.get("dimensions")
    filters = args.get("filters")
    result = await GoogleAnalyticsService(ctx).report(
        _property_arg(args),
        dimensions=[str(d) for d in dimensions] if isinstance(dimensions, list) else [],
        metrics=[str(m) for m in metrics] if isinstance(metrics, list) else [],
        period=str(args.get("period")) if args.get("period") else None,
        limit=_limit(args.get("limit"), 25),
        order=str(args.get("order")) if args.get("order") else None,
        filters=[str(f) for f in filters] if isinstance(filters, list) else [],
    )
    return ToolResult(data=result.model_dump(mode="json"))


def _limit(raw: Any, fallback: int) -> int:
    try:
        return max(1, min(250, int(raw)))
    except (TypeError, ValueError):
        return fallback


GOOGLE_ANALYTICS_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="google_analytics.properties",
        description=(
            "List the Google Analytics 4 properties the connected Google account can read, "
            "optionally filtered by name. Start here: every other Analytics tool takes a "
            "property_id from this list. If it answers connected=false or "
            "has_analytics_access=false, say which one and stop — there is nothing to report on "
            "until somebody connects or re-consents."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": ["string", "null"]}},
            "required": [],
            "additionalProperties": False,
        },
        handler=_properties,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_analytics.overview",
        description=(
            "How a GA4 property did over a period, compared with the same period a year earlier "
            "(or the period immediately before), with the change per metric already computed, "
            "plus sessions by acquisition channel. Rates such as engagementRate and bounceRate "
            "are fractions (0.4595 = 45,95%); a null relative change means there was no "
            "baseline, not that nothing changed. Read warnings: GA4 sometimes samples or "
            "withholds part of an answer, and a sampled number is an estimate."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
                "compare": {"type": ["string", "null"], "enum": ["year", "previous", None]},
            },
            "required": ["property_id"],
            "additionalProperties": False,
        },
        handler=_overview,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_analytics.breakdown",
        description=(
            "One dimension of a GA4 property, ranked: top pages (pagePath), landing pages "
            "(landingPage), channels (sessionDefaultChannelGroup), traffic sources "
            "(sessionSource), devices (deviceCategory), countries (country), events "
            "(eventName), or any custom dimension. row_count and truncated tell you whether the "
            "rows returned are the whole list."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "dimension": {"type": "string"},
                "metrics": {"type": ["array", "null"], "items": {"type": "string"}},
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
            },
            "required": ["property_id", "dimension"],
            "additionalProperties": False,
        },
        handler=_breakdown,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_analytics.timeseries",
        description=(
            "The chosen GA4 metrics day by day, oldest first — for a question about a trend "
            "rather than a total. Defaults to sessions, totalUsers and keyEvents."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "metrics": {"type": ["array", "null"], "items": {"type": "string"}},
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
            },
            "required": ["property_id"],
            "additionalProperties": False,
        },
        handler=_timeseries,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_analytics.setup",
        description=(
            "Whether a GA4 property is actually measuring anything and whether it measures what "
            "the client was promised: its data streams and measurement IDs, the events counted "
            "as conversions (GA4 calls them key events), the Google Ads links, and how long "
            "event data is kept. Use this for 'is the tracking working', 'why are there no "
            "conversions' and 'why is paid traffic missing' — an unlinked Ads account cannot be "
            "attributed however much it spent, and a property with no key events counts no "
            "conversions however many the report quotes."
        ),
        input_schema={
            "type": "object",
            "properties": {"property_id": {"type": "string"}},
            "required": ["property_id"],
            "additionalProperties": False,
        },
        handler=_setup,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_analytics.realtime",
        description=(
            "Who is on the site in the last thirty minutes, optionally split by a realtime "
            "dimension (unifiedScreenName, country, deviceCategory). There is no period: this "
            "answers about now and about nothing else, so never use it for a trend."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "dimensions": {"type": ["array", "null"], "items": {"type": "string"}},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 50},
            },
            "required": ["property_id"],
            "additionalProperties": False,
        },
        handler=_realtime,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_analytics.report",
        description=(
            "Any GA4 dimensions crossed with any metrics over any period — the escape hatch for "
            "questions the other Analytics tools do not answer. Filters are strings: "
            "name==value (exact), name=@value (contains), name=^value (begins with). GA4 refuses "
            "unknown or incompatible field combinations, so prefer the curated tools where they "
            "fit. Requires its own permission, separate from reading Analytics."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "property_id": {"type": "string"},
                "metrics": {"type": "array", "items": {"type": "string"}},
                "dimensions": {"type": ["array", "null"], "items": {"type": "string"}},
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
                "order": {"type": ["string", "null"]},
                "filters": {"type": ["array", "null"], "items": {"type": "string"}},
            },
            "required": ["property_id", "metrics"],
            "additionalProperties": False,
        },
        handler=_report,
        permission=_RUN,
    ),
]
