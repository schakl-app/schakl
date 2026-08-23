"""Executable AI tools this module contributes (CLAUDE.md §6, §12; issue #127).

Each entry is an :class:`AIToolSpec` on the descriptor's ``mcp_tools``, which is what the
in-app assistant offers the model. **Every one is a read**, and that is a decision rather than
an omission: the writes this module owns are linking a client to somebody else's advertising
account and rewording what a dashboard shows, both of which are configuration a person makes
once and neither of which a model should reach for because a sentence sounded like a request.

**RBAC is the filter, not the docstring.** Each spec names the permission its handler's service
is about to demand — ``marketing.metrics.read`` for a client's numbers, ``marketing.overview
.read`` for the cross-client grid, which is a manager permission here (docs/UX.md). A caller
holding neither never sees the tool at all, and the service's own ``ctx.require`` refuses it a
second time if it somehow did: the filter keeps a tool the caller may never use out of the
model's view, and the service keeps the answer correct. The company horizon (#285) rides along
untouched, because every handler goes through :class:`MarketingService` rather than around it —
a member scoped to one client group is offered these tools and answered about their own clients.

The shapes are the ones a marketeer actually asks for, and two of them exist because the
alternative is arithmetic the model should not be doing: ``marketing.performance`` returns the
period, the compared period **and** the delta already computed, and ``marketing.overview`` ranks
clients server-side. What is deliberately *not* returned by default is the daily series — a
year of six metrics per source is thousands of numbers that answer no question anybody asked out
loud, so ``include_series`` is opt-in and capped.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.ai import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.marketing.schemas import KpiValue, SourceMetrics
from app.modules.marketing.service import MarketingService

_READ = "marketing.metrics.read"
_OVERVIEW = "marketing.overview.read"

#: How much of a daily series a tool will hand a model when one is asked for. A period is
#: capped at 400 days upstream; six metrics over that is 2 400 numbers, and a model that is
#: handed them will summarise them badly rather than ask for the fold it already has.
_MAX_SERIES_DAYS = 92

_PERIOD_DESCRIPTION = (
    "The span to report on: a trailing window (30d, 90d, 365d), a preset (month, last_month, "
    "quarter, last_quarter) or a named calendar period (2026-07, 2026-Q3). An unknown value "
    "falls back to 30d."
)


def _uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        raise AppError("validation", "errors.validation", status_code=422) from None


def _period(args: dict[str, Any]) -> str | None:
    raw = args.get("period")
    return str(raw) if raw else None


def _kpi(value: KpiValue) -> dict[str, Any]:
    """One KPI as the model should read it: both numbers and the change between them.

    ``delta_pct`` is ``None`` rather than zero when there is no baseline — a percentage against
    nothing is undefined, and a model handed a zero writes a sentence claiming the client stood
    still (the ``google_ads.overview`` rule, one module over).
    """
    return {
        "current": value.current,
        "previous": value.previous,
        "delta_pct": value.delta_pct,
        "lower_is_better": value.lower_is_better,
    }


def _source_row(metrics: SourceMetrics, *, include_series: bool) -> dict[str, Any]:
    row: dict[str, Any] = {
        "link_id": str(metrics.link_id),
        "source": metrics.source.value,
        "display_name": metrics.display_name,
        "external_id": metrics.external_id,
        "website": metrics.website_name,
        # What the numbers are worth: a link that has never synced answers zeroes, and a model
        # that cannot tell that from a client with no traffic will report the second.
        "health": metrics.health,
        "last_error": metrics.last_error,
        "last_synced_at": (
            metrics.last_synced_at.isoformat() if metrics.last_synced_at else None
        ),
        "currency": metrics.currency,
        "primary_metric": metrics.primary_metric,
        "kpis": {key: _kpi(value) for key, value in metrics.kpis.items()},
        "channels": metrics.channels,
        # The kinds `marketing.drilldown` will accept for this link, so the follow-up call is
        # never a guess against an adapter's private vocabulary.
        "drilldowns": list(metrics.drilldowns),
    }
    if include_series:
        dates = metrics.series.dates[-_MAX_SERIES_DAYS:]
        row["series"] = {
            "dates": [day.isoformat() for day in dates],
            "metrics": {
                key: values[-_MAX_SERIES_DAYS:] for key, values in metrics.series.metrics.items()
            },
        }
    return row


async def _clients(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """Grounding: which clients have marketing data at all, and from where."""
    query = str(args.get("query") or "").strip().casefold()
    listing = await MarketingService(ctx).linked_clients(200)
    rows = [
        row for row in listing.rows if not query or query in row.company_name.casefold()
    ][:50]
    return ToolResult(
        data={
            "clients": [
                {
                    "company_id": str(row.company_id),
                    "company_name": row.company_name,
                    "sources": [
                        {
                            "source": item.source.value,
                            "links": item.links,
                            "state": item.state,
                        }
                        for item in row.sources
                    ],
                }
                for row in rows
            ],
            "total_linked": listing.total,
        },
        sources=tuple(
            Source(type="company", id=str(row.company_id), label=row.company_name)
            for row in rows
        ),
    )


async def _performance(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """One client's marketing performance, per connected source, with the change computed."""
    company_id = _uuid(args.get("company_id"))
    include_series = bool(args.get("include_series"))
    result = await MarketingService(ctx).company_marketing(company_id, 30, _period(args))
    window = result.compare
    return ToolResult(
        data={
            "company_id": str(result.company_id),
            "period": {
                "from": window.current_start.isoformat(),
                "to": window.current_end.isoformat(),
                "days": result.range_days,
            },
            # Named, never implied: "up 20 %" over an unstated span is not a claim anyone can
            # check, and this client may compare against last year while the next one does not.
            "compared_with": {
                "from": window.start.isoformat(),
                "to": window.end.isoformat(),
                "mode": window.mode.value,
            },
            "sources": [
                _source_row(item, include_series=include_series)
                for item in result.sources
                if not item.hidden
            ],
            "needs_connection": result.needs_connection,
        },
        sources=(Source(type="company", id=str(result.company_id), label=""),),
    )


async def _drilldown(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """The table behind a tile: top pages, channels, devices, queries, keywords, campaigns."""
    company_id = _uuid(args.get("company_id"))
    link_id = _uuid(args.get("link_id"))
    kind = str(args.get("kind") or "").strip()
    table = await MarketingService(ctx).drilldown(
        company_id, link_id, kind, 30, _period(args)
    )
    return ToolResult(
        data={
            "source": table.source.value,
            "kind": table.kind,
            "columns": list(table.columns),
            "rows": [
                {"label": row.label, "metrics": row.metrics} for row in table.rows[:50]
            ],
            # A drill-down a client's own settings have switched off, or one the source cannot
            # answer live, is *unavailable* rather than empty — different sentences.
            "available": table.available,
            "unavailable_reason": table.unavailable_reason,
        },
    )


async def _connections(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """What is connected for one client, and which source answers keyword positions.

    The second half is the part worth a tool: a keyword table may come from SE Ranking or from
    Search Console (#373), the choice is a resolved setting rather than a fact about the data,
    and two months on different sources are not comparable. A model that reports a ranking
    without knowing which register it came from is reporting half a number.
    """
    company_id = _uuid(args.get("company_id"))
    settings = await MarketingService(ctx).company_settings(company_id)
    return ToolResult(
        data={
            "company_id": str(settings.company_id),
            "linked_sources": [item.value for item in settings.linked_sources],
            "links": [
                {
                    "link_id": str(link.id),
                    "source": link.source.value,
                    "display_name": link.display_name,
                }
                for link in settings.links
            ],
            "keyword_source": (
                settings.keyword_source.value if settings.keyword_source else None
            ),
            "keyword_settings": settings.rankings_resolved.model_dump(mode="json"),
            "compare": settings.compare_resolved.value,
        },
    )


async def _summary(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """The headline per client, ranked — the digest the My Day widget draws."""
    try:
        limit = max(1, min(20, int(args.get("limit") or 5)))
    except (TypeError, ValueError):
        limit = 5
    digest = await MarketingService(ctx).summary(30, limit, _period(args))
    return ToolResult(
        data={
            "period": {
                "from": digest.compare.current_start.isoformat(),
                "to": digest.compare.current_end.isoformat(),
            },
            "compared_with": {
                "from": digest.compare.start.isoformat(),
                "to": digest.compare.end.isoformat(),
                "mode": digest.compare.mode.value,
            },
            "linked_total": digest.linked_total,
            "rows": [
                {
                    "company_id": str(row.company_id),
                    "company_name": row.company_name,
                    "metric": row.metric,
                    "value": _kpi(row.kpi),
                }
                for row in digest.rows
            ],
        },
        sources=tuple(
            Source(type="company", id=str(row.company_id), label=row.company_name)
            for row in digest.rows
        ),
    )


async def _overview(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """Every linked client on one grid, ranked server-side."""
    sort = args.get("sort")
    result = await MarketingService(ctx).overview(30, str(sort) if sort else None, _period(args))
    return ToolResult(
        data={
            "period": {
                "from": result.compare.current_start.isoformat(),
                "to": result.compare.current_end.isoformat(),
                "days": result.range_days,
            },
            "compared_with": {
                "from": result.compare.start.isoformat(),
                "to": result.compare.end.isoformat(),
                "mode": result.compare.mode.value,
            },
            "rows": [
                {
                    "company_id": str(row.company_id),
                    "company_name": row.company_name,
                    "sources": [item.value for item in row.sources_present],
                    "metrics": {key: _kpi(value) for key, value in row.metrics.items()},
                }
                for row in result.rows[:100]
            ],
            "total": result.total,
        },
        sources=tuple(
            Source(type="company", id=str(row.company_id), label=row.company_name)
            for row in result.rows[:100]
        ),
    )


MARKETING_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="marketing.clients",
        description=(
            "List the clients that have marketing sources connected (GA4, Search Console, "
            "Google Ads, Rank Math, SE Ranking), optionally filtered by client name. Start "
            "here: every other marketing tool takes a company_id from this list."
        ),
        input_schema={
            "type": "object",
            "properties": {"query": {"type": ["string", "null"]}},
            "required": [],
            "additionalProperties": False,
        },
        handler=_clients,
        permission=_READ,
    ),
    AIToolSpec(
        name="marketing.performance",
        description=(
            "How one client's marketing is doing over a period, per connected source, with the "
            "comparison period and the change per metric already computed. Returns the KPIs the "
            "client's own dashboard shows, the acquisition channel split, and which drill-downs "
            "each source can answer. Rates such as engagement_rate and ctr are fractions "
            "(0.0453 = 4.53%); a null delta_pct means not computable, not zero; health tells you "
            "whether the numbers are current."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
                "include_series": {
                    "type": ["boolean", "null"],
                    "description": (
                        "Also return the daily numbers (capped at the last 92 days). Off by "
                        "default: ask for it only when the question is about a trend."
                    ),
                },
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_performance,
        permission=_READ,
    ),
    AIToolSpec(
        name="marketing.drilldown",
        description=(
            "The table behind one tile of a client's marketing dashboard: top pages, channel "
            "split, devices, key events, search queries, landing pages, keywords or campaigns, "
            "depending on the source. Takes a link_id and a kind from marketing.performance's "
            "drilldowns list — a kind that source does not offer is refused."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "link_id": {"type": "string"},
                "kind": {"type": "string"},
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
            },
            "required": ["company_id", "link_id", "kind"],
            "additionalProperties": False,
        },
        handler=_drilldown,
        permission=_READ,
    ),
    AIToolSpec(
        name="marketing.connections",
        description=(
            "What is connected for one client and how their reporting is configured: the linked "
            "sources with their link_ids, which source answers keyword positions (SE Ranking or "
            "Search Console — two months on different sources are not comparable), the keyword "
            "depth settings, and the comparison period their dashboard measures against."
        ),
        input_schema={
            "type": "object",
            "properties": {"company_id": {"type": "string"}},
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_connections,
        permission=_READ,
    ),
    AIToolSpec(
        name="marketing.summary",
        description=(
            "The headline marketing number per client, best first — sessions where GA4 is "
            "linked, otherwise Search Console clicks. A digest for 'how are the clients doing "
            "this month' without asking client by client."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
                "limit": {"type": ["integer", "null"], "minimum": 1, "maximum": 20},
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_summary,
        permission=_READ,
    ),
    AIToolSpec(
        name="marketing.overview",
        description=(
            "The cross-client grid: one row per client with a source linked, every headline "
            "metric and its change, sorted server-side. Use this to rank or compare clients; "
            "use marketing.performance for one client in depth. Requires the marketing overview "
            "permission, which is a manager one — it is not implied by reading a client's own "
            "dashboard."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "period": {"type": ["string", "null"], "description": _PERIOD_DESCRIPTION},
                "sort": {
                    "type": ["string", "null"],
                    "description": (
                        "company_name | sessions | clicks | position | cost | conversions; "
                        "prefix with - for descending."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        },
        handler=_overview,
        permission=_OVERVIEW,
    ),
]
