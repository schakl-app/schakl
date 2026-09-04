"""Curated AI tools for Google Search Console. Business-licensed — see LICENSE.

**These reach the in-app assistant, and the MCP server gets the routes instead.** ``mcp_tools``
is read by ``app/core/ai/tools.py`` alone; ``/mcp`` is built off the OpenAPI document, so an
external client gets the thirteen operations in ``router.py`` at ``/mcp/google-search-console``
and none of the seven below. Both catalogs are real and neither is the other's summary — the
arrangement ``google_analytics`` documents, for the same reason.

What earns a place here is the question a marketeer actually asks the box in the corner of the
screen — "how did klant.nl do in Google last month", "why is this page not indexed", "how
visible are we in AI Overviews" — and the last of those is the one that most needs a tool,
because it is the one a model would otherwise answer from the web totals with a confident and
wrong number. ``google_search_console.ai_visibility`` answers the state instead.

Every handler runs under the caller's own :class:`RequestContext` and its spec names the
permission the service is about to demand, so a caller holding neither key is never told these
tools exist (§15). Read-only, all seven.
"""

from __future__ import annotations

from typing import Any

from app.core.ai import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.integrations.google_search_console.service import GoogleSearchConsoleService

_READ = "google_search_console.site.read"
_RUN = "google_search_console.report.run"

_PERIOD_DESCRIPTION = (
    "30d, 90d, 365d, month, last_month, quarter, last_quarter, or a named period like "
    "2026-07 or 2026-Q3. The last two or three days are still being collected."
)
_SITE_DESCRIPTION = (
    "The property as google_search_console.sites lists it: sc-domain:klant.nl or "
    "https://www.klant.nl/. A bare hostname is read as the domain property."
)


def _site_arg(args: dict[str, Any]) -> str:
    return str(args.get("site") or "").strip()


def _text(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    return str(value) if value else None


def _limit(raw: Any, fallback: int) -> int:
    try:
        return max(1, min(250, int(raw)))
    except (TypeError, ValueError):
        return fallback


async def _sites(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    listing = await GoogleSearchConsoleService(ctx).sites(args.get("query"))
    if not listing.connected or not listing.has_scope:
        # A state, not an error: the caller can act on exactly one of these two and the
        # difference is which button they press (#411).
        return ToolResult(
            data={
                "connected": listing.connected,
                "has_search_console_access": listing.has_scope,
                "error": (
                    "errors.google_not_connected"
                    if not listing.connected
                    else "errors.google_search_console_scope_missing"
                ),
                "sites": [],
            }
        )
    rows = listing.sites[:50]
    return ToolResult(
        data={
            "sites": [row.model_dump(mode="json") for row in rows],
            "shown": len(rows),
            "total": len(listing.sites),
            "truncated": len(rows) < len(listing.sites),
        },
        sources=tuple(
            Source(type="search_console_site", id=row.site_url, label=row.display_name)
            for row in rows
        ),
    )


async def _overview(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    result = await GoogleSearchConsoleService(ctx).overview(
        _site_arg(args),
        period=_text(args, "period"),
        compare=_text(args, "compare"),
        search_type=_text(args, "search_type"),
    )
    return ToolResult(
        data=result.model_dump(mode="json"),
        sources=(Source(type="search_console_site", id=result.site_url, label=""),),
    )


async def _breakdown(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    filters = args.get("filters")
    result = await GoogleSearchConsoleService(ctx).breakdown(
        _site_arg(args),
        dimension=str(args.get("dimension") or "query"),
        period=_text(args, "period"),
        search_type=_text(args, "search_type"),
        filters=[str(f) for f in filters] if isinstance(filters, list) else [],
        limit=_limit(args.get("limit"), 10),
        order=_text(args, "order"),
    )
    return ToolResult(data=result.model_dump(mode="json"))


async def _movers(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    result = await GoogleSearchConsoleService(ctx).movers(
        _site_arg(args),
        period=_text(args, "period"),
        compare=_text(args, "compare"),
        dimension=str(args.get("dimension") or "query"),
        limit=_limit(args.get("limit"), 10),
    )
    return ToolResult(data=result.model_dump(mode="json"))


async def _inspect(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    result = await GoogleSearchConsoleService(ctx).inspect(
        _site_arg(args), url=str(args.get("url") or ""), language=_text(args, "language")
    )
    return ToolResult(
        data=result.model_dump(mode="json"),
        sources=(Source(type="url", id=result.inspected_url, label=result.inspected_url),),
    )


async def _ai_visibility(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    result = await GoogleSearchConsoleService(ctx).ai_visibility(
        _site_arg(args), period=_text(args, "period"), compare=_text(args, "compare")
    )
    return ToolResult(data=result.model_dump(mode="json"))


async def _query(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    dimensions = args.get("dimensions")
    filters = args.get("filters")
    result = await GoogleSearchConsoleService(ctx).query(
        _site_arg(args),
        dimensions=[str(d) for d in dimensions] if isinstance(dimensions, list) else [],
        period=_text(args, "period"),
        search_type=_text(args, "search_type"),
        filters=[str(f) for f in filters] if isinstance(filters, list) else [],
        aggregation=_text(args, "aggregation"),
        data_state=_text(args, "data_state"),
        limit=_limit(args.get("limit"), 25),
    )
    return ToolResult(data=result.model_dump(mode="json"))


_SITE_PROPERTY = {"type": "string", "description": _SITE_DESCRIPTION}
_PERIOD_PROPERTY = {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION}
_COMPARE_PROPERTY = {"type": ["string", "null"], "enum": ["year", "previous", None]}
_SEARCH_TYPE_PROPERTY = {
    "type": ["string", "null"],
    "enum": ["web", "image", "video", "news", "discover", "googleNews", None],
}

GOOGLE_SEARCH_CONSOLE_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="google_search_console.sites",
        description=(
            "List the Search Console properties the connected Google account can read, "
            "optionally filtered by URL. Start here: every other Search Console tool takes a "
            "site from this list. If it answers connected=false or "
            "has_search_console_access=false, say which one and stop — there is nothing to "
            "report on until somebody connects or re-consents."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": ["string", "null"]}},
            "required": [],
            "additionalProperties": False,
        },
        handler=_sites,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_search_console.overview",
        description=(
            "How a site did in Google Search over a period — clicks, impressions, CTR and "
            "average position — compared with the same period a year earlier (or the period "
            "immediately before), with the change per metric already computed, plus the split "
            "by device. ctr is a fraction (0.0432 = 4,32%); position is an average where lower "
            "is better. A null relative change means there was no baseline. fresh_from names "
            "the first day Google is still collecting."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "site": _SITE_PROPERTY,
                "period": _PERIOD_PROPERTY,
                "compare": _COMPARE_PROPERTY,
                "search_type": _SEARCH_TYPE_PROPERTY,
            },
            "required": ["site"],
            "additionalProperties": False,
        },
        handler=_overview,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_search_console.breakdown",
        description=(
            "One dimension of a site's Google Search performance, ranked by clicks: the top "
            "queries (query), top pages (page), countries, devices or search appearances "
            "(searchAppearance). Filters are strings such as query=@merknaam (contains), "
            "page==https://…/ (exact), country==nld, query!@merknaam (not contains). Use "
            "order=-impressions to rank by impressions instead. truncated tells you whether the "
            "rows are the whole list."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "site": _SITE_PROPERTY,
                "dimension": {
                    "type": ["string", "null"],
                    "enum": ["query", "page", "country", "device", "searchAppearance", None],
                },
                "period": _PERIOD_PROPERTY,
                "search_type": _SEARCH_TYPE_PROPERTY,
                "filters": {"type": ["array", "null"], "items": {"type": "string"}},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
                "order": {"type": ["string", "null"]},
            },
            "required": ["site"],
            "additionalProperties": False,
        },
        handler=_breakdown,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_search_console.movers",
        description=(
            "Which queries (or pages) climbed or fell most in average Google position between "
            "the period and the one it is compared against. change is positive for a climb. "
            "entered and dropped count terms that appeared or vanished entirely. Use this for "
            "'what changed in the rankings' and 'which keywords did we lose'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "site": _SITE_PROPERTY,
                "period": _PERIOD_PROPERTY,
                "compare": _COMPARE_PROPERTY,
                "dimension": {"type": ["string", "null"], "enum": ["query", "page", None]},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
            },
            "required": ["site"],
            "additionalProperties": False,
        },
        handler=_movers,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_search_console.inspect_url",
        description=(
            "What Google's index holds for one page: whether it is indexed (verdict PASS) and "
            "if not why (coverage_state, indexing_state, robots_txt_state, page_fetch_state), "
            "which canonical Google chose against the one the page declares, when it was last "
            "crawled, and the rich results found. One URL per call, and it must be under the "
            "site. Use this for 'why is this page not in Google'."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "site": _SITE_PROPERTY,
                "url": {"type": "string"},
                "language": {"type": ["string", "null"]},
            },
            "required": ["site", "url"],
            "additionalProperties": False,
        },
        handler=_inspect,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_search_console.ai_visibility",
        description=(
            "How visible a site is in Google's generative AI features (AI Overviews, AI Mode), "
            "as far as the Search Console API can say. Read available first: while it is "
            "false, the numbers exist only in Search Console's own Generative AI performance "
            "report — hand over report_url and say so. Never estimate AI visibility from the "
            "web totals; AI Overviews are folded into them and cannot be separated. Rank Math "
            "AI Visibility on the marketing dashboard is a different measurement (answers by "
            "AI assistants about the brand), not this."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "site": _SITE_PROPERTY,
                "period": _PERIOD_PROPERTY,
                "compare": _COMPARE_PROPERTY,
            },
            "required": ["site"],
            "additionalProperties": False,
        },
        handler=_ai_visibility,
        permission=_READ,
    ),
    AIToolSpec(
        name="google_search_console.query",
        description=(
            "Any Search Console dimensions crossed (query, page, country, device, "
            "searchAppearance, date, hour) with any filters, aggregation and data state over "
            "any period — the escape hatch for questions the other Search Console tools do not "
            "answer, such as queries per page or branded queries by device. Requires its own "
            "permission, separate from reading Search Console."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "site": _SITE_PROPERTY,
                "dimensions": {"type": ["array", "null"], "items": {"type": "string"}},
                "period": _PERIOD_PROPERTY,
                "search_type": _SEARCH_TYPE_PROPERTY,
                "filters": {"type": ["array", "null"], "items": {"type": "string"}},
                "aggregation": {"type": ["string", "null"]},
                "data_state": {
                    "type": ["string", "null"],
                    "enum": ["all", "final", "hourly_all", None],
                },
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 250},
            },
            "required": ["site"],
            "additionalProperties": False,
        },
        handler=_query,
        permission=_RUN,
    ),
]
