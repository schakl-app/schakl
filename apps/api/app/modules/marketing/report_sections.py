"""What ``marketing`` contributes to a periodic client report (issue #300).

The panels pattern, applied to documents: the reporting module composes whatever sections the
enabled modules declare and knows the name of no module. Adding "zoekwoordposities" to every
client's monthly report is therefore a change *here*, where rankings live, and not an edit to
reporting.

**One gather, many sections.** A section provider is called once per section, but a report
needs ten answers from three credentials, and opening a Google session per section would pay
a token refresh each time. So every provider below funnels through :func:`gather`, memoised
per request against the ``RequestContext`` — the whole report costs one Google session, one
SE Ranking session, and two indexed reads of the stored daily rows.

**Stored where stored will do, live only where it must.** Channel traffic and Search Console
come out of ``marketing_metrics_daily`` for both periods, which is what makes a report of last
March still printable next March. Only the tier-2 splits that were never warehoused — traffic
by *source*, per-keyword positions, the audit, AI-search presence — are fetched live, and the
report freezes them into its own snapshot the moment it has them.

The payload convention every provider returns (or ``None`` for "this client has none of this"):

    {"kind": str,                 # picks the renderer's block
     "columns": [str],            # metric keys, labelled by the document's locale
     "rows": [ {...} ],
     "totals": {metric: float},
     "compare": {metric: float} | None,
     "chart": {...} | None,       # a chart spec, turned into inline SVG by the renderer
     "notes": [ {code, detail} ]} # for the run's warnings — never printed to the client
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from weakref import WeakKeyDictionary

from sqlalchemy import select

from app.core.tenancy import RequestContext
from app.modules.google import client as google_client
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.marketing.layout import resolved_tiles, source_layout
from app.modules.marketing.models import (
    MarketingCompanySettings,
    MarketingLink,
    MarketingMetricDaily,
    MarketingSource,
)
from app.modules.marketing.service import (
    aggregate,
    org_key_client,
    resolve_seranking_key,
)
from app.modules.marketing.sources import source_for
from app.registry import AUDIENCE_INTERNAL, ReportSectionSpec, ReportWindow

logger = logging.getLogger("schakl.marketing")

#: The GA4 splits a client report breaks out, and the section each one feeds.
_GA4_LIVE_KINDS = ("organic_sources", "social_sources", "referral_sources", "key_events")

#: A report table is a page of a PDF, not a database. Past this a table stops being readable
#: and starts being a data dump — and the overflow is *reported* (§17: a cap that truncates
#: says so), on the run's warnings where the agency sees it, never on the client's document.
MAX_TABLE_ROWS = 25
MAX_KEYWORD_ROWS = 200

#: Memoised per request, so ten sections cost one gather. Weak keys, so a finished request's
#: payload is collected with it rather than lingering in a module-level dict.
_CACHE: WeakKeyDictionary[RequestContext, dict[tuple, GatheredMarketing]] = (
    WeakKeyDictionary()
)


@dataclass
class GatheredMarketing:
    """Everything the marketing sections need for one client and one period pair."""

    links: list[MarketingLink] = field(default_factory=list)
    #: ``{source: {"totals": {...}, "compare": {...}, "channels": {...},
    #:             "compare_channels": {...}, "days": int}}``
    stored: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: ``{kind: {"rows": [...], "compare_rows": [...]}}`` — the live GA4 splits.
    live: dict[str, dict[str, Any]] = field(default_factory=dict)
    keywords: list[dict[str, Any]] = field(default_factory=list)
    audit: dict[str, Any] | None = None
    ai_search: list[dict[str, Any]] = field(default_factory=list)
    key_event_labels: dict[str, str] = field(default_factory=dict)
    show_conversions: bool = True
    notes: list[dict[str, str]] = field(default_factory=list)


async def gather(ctx: RequestContext, window: ReportWindow) -> GatheredMarketing:
    """Everything, once. Memoised on ``(company, period)`` for the life of the request."""
    key = (window.company_id, window.start, window.end, window.compare_start)
    per_ctx = _CACHE.setdefault(ctx, {})
    if key not in per_ctx:
        per_ctx[key] = await _gather(ctx, window)
    return per_ctx[key]


async def _gather(ctx: RequestContext, window: ReportWindow) -> GatheredMarketing:
    out = GatheredMarketing()
    # Through the repo, so the company horizon applies: a restricted member generating a
    # report gets their own clients' links or nothing (§15, #285).
    stmt = (
        ctx.repo(MarketingLink)
        .scoped_select()
        .where(
            MarketingLink.company_id == window.company_id,
            MarketingLink.active.is_(True),
        )
    )
    out.links = list((await ctx.session.execute(stmt)).scalars().all())
    if not out.links:
        return out

    settings_row = await ctx.session.scalar(
        select(MarketingCompanySettings).where(
            MarketingCompanySettings.org_id == ctx.org.id,
            MarketingCompanySettings.company_id == window.company_id,
        )
    )
    layout = settings_row.layout if settings_row else None
    show_key_events = settings_row.show_key_events if settings_row else True
    ga4_layout = source_layout(layout, MarketingSource.GA4.value)
    # The client's curated tab decides what the report shows too, so a tile the agency hid
    # from this client's dashboard cannot reappear in their PDF (#192).
    ga4_tiles = resolved_tiles(MarketingSource.GA4.value, ga4_layout, show_key_events)
    out.show_conversions = "keyEvents" in ga4_tiles or "conversions" in ga4_tiles
    if ga4_layout is not None and getattr(ga4_layout, "event_labels", None):
        out.key_event_labels = {
            str(name): str(labels.get(window.locale) or labels.get("nl") or "")
            for name, labels in (ga4_layout.event_labels or {}).items()
            if isinstance(labels, dict)
        }

    for link in out.links:
        out.stored[link.source] = await _stored(ctx, link, window)
        if link.last_synced_at is None:
            out.notes.append(
                {"code": "reporting.warning.never_synced", "detail": link.display_name}
            )
        elif link.last_error:
            out.notes.append(
                {"code": "reporting.warning.link_error", "detail": link.display_name}
            )

    await _gather_ga4_live(ctx, out, window)
    await _gather_seranking(ctx, out, window)
    return out


async def _stored(
    ctx: RequestContext, link: MarketingLink, window: ReportWindow
) -> dict[str, Any]:
    """Both periods of one link's daily rows, aggregated. Two indexed reads, never per day."""

    async def totals(start: date | None, end: date | None) -> tuple[dict, dict, int]:
        if start is None or end is None:
            return {}, {}, 0
        rows = list(
            (
                await ctx.session.execute(
                    select(MarketingMetricDaily.metrics).where(
                        MarketingMetricDaily.org_id == ctx.org.id,
                        MarketingMetricDaily.link_id == link.id,
                        MarketingMetricDaily.date >= start,
                        MarketingMetricDaily.date <= end,
                    )
                )
            )
            .scalars()
            .all()
        )
        channels: dict[str, float] = {}
        for row in rows:
            for name, sessions in (row.get("channels") or {}).items():
                channels[name] = channels.get(name, 0.0) + float(sessions or 0)
        return aggregate(link.source, rows), channels, len(rows)

    current, channels, days = await totals(window.start, window.end)
    compare, compare_channels, compare_days = await totals(
        window.compare_start, window.compare_end
    )
    return {
        "totals": current,
        "channels": channels,
        "compare": compare if compare_days else None,
        "compare_channels": compare_channels,
        "days": days,
        "compare_days": compare_days,
        "display_name": link.display_name,
    }


async def _gather_ga4_live(
    ctx: RequestContext, out: GatheredMarketing, window: ReportWindow
) -> None:
    """The four splits GA4 never warehoused, both periods, in one Google session."""
    link = next(
        (link for link in out.links if link.source == MarketingSource.GA4.value), None
    )
    if link is None or link.connection_id is None:
        return
    connection = await ctx.session.get(GoogleConnection, link.connection_id)
    if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
        out.notes.append({"code": "reporting.warning.disconnected", "detail": "ga4"})
        return
    adapter = source_for(MarketingSource.GA4.value)
    try:
        # The pool connection is handed back for the whole live block — this is a dozen GA4
        # calls and would otherwise pin a connection for the length of them (CLAUDE.md §11).
        async with (
            google_client.acting_as(ctx.session, ctx.org, connection) as gclient,
            ctx.release_db(),
        ):
            for kind in _GA4_LIVE_KINDS:
                if kind == "key_events" and not out.show_conversions:
                    continue
                current = await adapter.drilldown(
                    gclient, link.external_id, kind, window.start, window.end,
                    link.config or {},
                )
                compare_rows: list[dict[str, Any]] = []
                if window.compare_start and window.compare_end:
                    previous = await adapter.drilldown(
                        gclient, link.external_id, kind,
                        window.compare_start, window.compare_end, link.config or {},
                    )
                    compare_rows = [
                        {"label": row.label, **row.metrics} for row in previous.rows
                    ]
                out.live[kind] = {
                    "columns": current.columns,
                    "rows": [{"label": row.label, **row.metrics} for row in current.rows],
                    "compare_rows": compare_rows,
                }
    except Exception as exc:  # noqa: BLE001 — a report degrades, it never 500s
        logger.warning("reporting: GA4 live fetch failed for %s: %s", link.id, exc)
        out.notes.append({"code": "reporting.warning.source_failed", "detail": "ga4"})


async def _gather_seranking(
    ctx: RequestContext, out: GatheredMarketing, window: ReportWindow
) -> None:
    link = next(
        (link for link in out.links if link.source == MarketingSource.SERANKING.value), None
    )
    if link is None:
        return
    key = await resolve_seranking_key(ctx.session, ctx.org.id)
    if not key:
        out.notes.append({"code": "reporting.warning.seranking_not_configured", "detail": ""})
        return
    adapter = source_for(MarketingSource.SERANKING.value)
    try:
        async with org_key_client(key) as client, ctx.release_db():
            out.keywords = await adapter.keyword_rows(
                client, link.external_id, window.start, window.end
            )
            out.audit = await adapter.audit(client, link.external_id)
            out.ai_search = await adapter.ai_search(
                client, link.external_id, window.start, window.end
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("reporting: SE Ranking fetch failed for %s: %s", link.id, exc)
        out.notes.append({"code": "reporting.warning.source_failed", "detail": "seranking"})


# --------------------------------------------------------------------------------------- #
# Section providers
# --------------------------------------------------------------------------------------- #
def _capped(rows: list[dict], limit: int, out: GatheredMarketing, section: str) -> list[dict]:
    if len(rows) <= limit:
        return rows
    out.notes.append(
        {"code": "reporting.warning.truncated", "detail": f"{section}:{len(rows)}"}
    )
    return rows[:limit]


def _delta(current: float, previous: float | None) -> float | None:
    """Percentage change, or ``None`` when there is nothing honest to compare against.

    ``None`` rather than 0 or "N/A": a client whose previous year is genuinely empty should
    read *this* period's numbers as an achievement, not as an infinite rise from nothing.
    """
    if previous is None or previous == 0:
        return None
    return round(((current - previous) / previous) * 100, 1)


async def _traffic_channels(
    ctx: RequestContext, window: ReportWindow
) -> dict[str, Any] | None:
    data = await gather(ctx, window)
    stored = data.stored.get(MarketingSource.GA4.value)
    if not stored or not stored["channels"]:
        return None
    compare_channels = stored.get("compare_channels") or {}
    rows = [
        {
            "label": name,
            "sessions": round(sessions, 0),
            "compare_sessions": round(compare_channels.get(name, 0.0), 0),
            "delta": _delta(sessions, compare_channels.get(name) if compare_channels else None),
            "share": round(sessions / sum(stored["channels"].values()) * 100, 1)
            if sum(stored["channels"].values())
            else 0.0,
        }
        for name, sessions in sorted(
            stored["channels"].items(), key=lambda pair: pair[1], reverse=True
        )
    ]
    return {
        "kind": "channels",
        "columns": ["sessions", "compare_sessions", "delta", "share"],
        "rows": rows,
        "totals": stored["totals"],
        "compare": stored["compare"],
        "chart": {
            "type": "grouped",
            "labels": [row["label"] for row in rows],
            "series": [
                {"key": "current", "values": [row["sessions"] for row in rows]},
                {"key": "compare", "values": [row["compare_sessions"] for row in rows]},
            ],
        },
        "notes": data.notes,
    }


def _split_section(kind: str, chart: str | None, limit: int):  # noqa: ANN202
    """A traffic-by-source section — organic, social or referral. Same shape, three filters."""

    async def provider(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
        data = await gather(ctx, window)
        live = data.live.get(kind)
        if not live or not live["rows"]:
            return None
        compare = {row["label"]: row for row in live.get("compare_rows") or []}
        rows = []
        for row in live["rows"]:
            previous = compare.get(row["label"])
            rows.append(
                {
                    **row,
                    "compare_sessions": (previous or {}).get("sessions"),
                    "delta": _delta(
                        float(row.get("sessions") or 0),
                        (previous or {}).get("sessions") if previous else None,
                    ),
                }
            )
        rows = _capped(rows, limit, data, kind)
        payload: dict[str, Any] = {
            "kind": kind,
            "columns": live["columns"],
            "rows": rows,
            "totals": {},
            "compare": None,
            "chart": None,
            "notes": data.notes,
        }
        if chart == "share":
            payload["chart"] = {
                "type": "share",
                "items": [
                    {"label": row["label"], "value": float(row.get("sessions") or 0)}
                    for row in rows
                ],
            }
        elif chart == "grouped":
            payload["chart"] = {
                "type": "grouped",
                "labels": [row["label"] for row in rows[:8]],
                "series": [
                    {
                        "key": "current",
                        "values": [float(r.get("sessions") or 0) for r in rows[:8]],
                    },
                    {
                        "key": "compare",
                        "values": [float(r.get("compare_sessions") or 0) for r in rows[:8]],
                    },
                ],
            }
        return payload

    return provider


async def _conversions(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    data = await gather(ctx, window)
    if not data.show_conversions:
        return None
    live = data.live.get("key_events")
    if not live or not live["rows"]:
        return None
    compare = {row["label"]: row for row in live.get("compare_rows") or []}
    rows = [
        {
            # The tenant's own label for this event where they set one (#192), so the client's
            # dashboard and their PDF call the same thing by the same name.
            "label": data.key_event_labels.get(row["label"]) or row["label"],
            "keyEvents": float(row.get("keyEvents") or 0),
            "compare_keyEvents": float((compare.get(row["label"]) or {}).get("keyEvents") or 0),
            "delta": _delta(
                float(row.get("keyEvents") or 0),
                (compare.get(row["label"]) or {}).get("keyEvents")
                if row["label"] in compare
                else None,
            ),
        }
        for row in live["rows"]
    ]
    return {
        "kind": "conversions",
        "columns": ["keyEvents", "compare_keyEvents", "delta"],
        "rows": _capped(rows, MAX_TABLE_ROWS, data, "conversions"),
        "totals": {},
        "compare": None,
        "chart": {
            "type": "grouped",
            "labels": [row["label"] for row in rows[:8]],
            "series": [
                {"key": "current", "values": [row["keyEvents"] for row in rows[:8]]},
                {"key": "compare", "values": [row["compare_keyEvents"] for row in rows[:8]]},
            ],
        },
        "notes": data.notes,
    }


async def _rankings(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    data = await gather(ctx, window)
    if not data.keywords:
        return None
    rows = _capped(data.keywords, MAX_KEYWORD_ROWS, data, "rankings")
    groups: dict[str, list[dict]] = {}
    for row in rows:
        groups.setdefault(row["group"] or "", []).append(row)
    stored = data.stored.get(MarketingSource.SERANKING.value) or {}
    return {
        "kind": "rankings",
        "columns": ["begin", "end", "change"],
        "rows": rows,
        "groups": [
            {"name": name, "rows": members}
            for name, members in sorted(groups.items(), key=lambda pair: pair[0].lower())
        ],
        "totals": stored.get("totals") or {},
        "compare": stored.get("compare"),
        "chart": None,
        "notes": data.notes,
    }


async def _search_console(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    data = await gather(ctx, window)
    stored = data.stored.get(MarketingSource.GSC.value)
    if not stored or not stored["totals"]:
        return None
    return {
        "kind": "search_console",
        "columns": ["clicks", "impressions", "ctr", "position"],
        "rows": [],
        "totals": stored["totals"],
        "compare": stored["compare"],
        "chart": None,
        "notes": data.notes,
    }


async def _ai_search(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    data = await gather(ctx, window)
    if not data.ai_search:
        return None
    return {
        "kind": "ai_search",
        "columns": ["link_percent", "mention_percent"],
        "rows": data.ai_search,
        "totals": {},
        "compare": None,
        "chart": None,
        "notes": data.notes,
    }


async def _site_audit(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    """Internal only. A list of a client's technical faults is working material, not a
    deliverable — and reading it as one would have the client fixing our to-do list."""
    data = await gather(ctx, window)
    if data.audit is None:
        return None
    return {
        "kind": "audit",
        "columns": ["pages"],
        "rows": _capped(data.audit["findings"], MAX_TABLE_ROWS, data, "audit"),
        "totals": {
            "score": float(data.audit["score"]),
            "errors": float(data.audit["errors"]),
            "warnings": float(data.audit["warnings"]),
            "pages": float(data.audit["pages"]),
        },
        "compare": None,
        "chart": None,
        "audited_at": data.audit["audited_at"],
        "notes": data.notes,
    }


MARKETING_REPORT_SECTIONS: list[ReportSectionSpec] = [
    ReportSectionSpec(
        key="marketing.traffic_channels",
        title_key="reporting.section.traffic_channels",
        brief_key="reporting.brief.traffic_channels",
        provider=_traffic_channels,
        requires_permission="marketing.metrics.read",
        position=10,
    ),
    ReportSectionSpec(
        key="marketing.search_engines",
        title_key="reporting.section.search_engines",
        brief_key="reporting.brief.search_engines",
        provider=_split_section("organic_sources", "share", 10),
        requires_permission="marketing.metrics.read",
        position=20,
    ),
    ReportSectionSpec(
        key="marketing.rankings",
        title_key="reporting.section.rankings",
        brief_key="reporting.brief.rankings",
        provider=_rankings,
        requires_permission="marketing.metrics.read",
        position=30,
    ),
    ReportSectionSpec(
        key="marketing.search_console",
        title_key="reporting.section.search_console",
        brief_key="reporting.brief.search_console",
        provider=_search_console,
        requires_permission="marketing.metrics.read",
        position=40,
    ),
    ReportSectionSpec(
        key="marketing.referral",
        title_key="reporting.section.referral",
        brief_key="reporting.brief.referral",
        provider=_split_section("referral_sources", None, MAX_TABLE_ROWS),
        requires_permission="marketing.metrics.read",
        position=50,
    ),
    ReportSectionSpec(
        key="marketing.social",
        title_key="reporting.section.social",
        brief_key="reporting.brief.social",
        provider=_split_section("social_sources", "grouped", 10),
        requires_permission="marketing.metrics.read",
        position=60,
    ),
    ReportSectionSpec(
        key="marketing.conversions",
        title_key="reporting.section.conversions",
        brief_key="reporting.brief.conversions",
        provider=_conversions,
        requires_permission="marketing.metrics.read",
        position=70,
    ),
    ReportSectionSpec(
        key="marketing.ai_search",
        title_key="reporting.section.ai_search",
        brief_key="reporting.brief.ai_search",
        provider=_ai_search,
        requires_permission="marketing.metrics.read",
        position=80,
    ),
    ReportSectionSpec(
        key="marketing.site_audit",
        title_key="reporting.section.site_audit",
        brief_key="reporting.brief.site_audit",
        provider=_site_audit,
        audience=AUDIENCE_INTERNAL,
        requires_permission="marketing.metrics.read",
        position=90,
    ),
]


def clear_cache(ctx: RequestContext, company_id: uuid.UUID | None = None) -> None:
    """Drop the memoised gather — used between clients in a batch run."""
    if company_id is None:
        _CACHE.pop(ctx, None)
        return
    per_ctx = _CACHE.get(ctx) or {}
    for key in [key for key in per_ctx if key[0] == company_id]:
        per_ctx.pop(key, None)
