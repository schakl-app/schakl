"""marketing service (epic #134): links, pickers, stored-metric reads, live drill-downs, overview.

Two rules shape everything here:

- **The panel/tab/overview read *our* database only.** Trends, deltas and the cross-client grid
  come from ``marketing_metrics_daily`` — one query per screen, zero Google calls
  (docs/PERFORMANCE.md). Only the pickers and the tier-2 drill-downs touch Google, and those are
  Redis-cached.
- **A period total re-derives averages, never sums them.** Average position / CTR / engagement
  over N days is impression- or session-weighted, not the sum of N daily values.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.activity import ActivityService
from app.core.cache import get_redis
from app.core.crypto import decrypt, encrypt
from app.core.jobs import enqueue
from app.core.tenancy import RequestContext
from app.core.timezone import org_zoneinfo
from app.errors import AppError
from app.modules.companies.models import Company
from app.modules.google import client as google_client
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.marketing.layout import (
    GA4_KEY_EVENT_DRILLDOWN,
    GA4_KEY_EVENT_TILES,
    CompanyLayout,
    resolved_drilldowns,
    resolved_primary,
    resolved_tiles,
    source_layout,
    validate_layout,
)
from app.modules.marketing.models import (
    MarketingCompanySettings,
    MarketingLink,
    MarketingMetricDaily,
    MarketingSettings,
    MarketingSource,
)
from app.modules.marketing.schemas import (
    AccountsResponse,
    AvailableAccount,
    CompanyMarketing,
    CompanySettingsRead,
    DrilldownResponse,
    DrilldownRowOut,
    KpiValue,
    LinkCreate,
    LinkRead,
    MarketingSettingsRead,
    MarketingSettingsWrite,
    OverviewResponse,
    OverviewRow,
    SeriesData,
    SourceMetrics,
)
from app.modules.marketing.sources import source_for
from app.modules.marketing.sources.base import (
    AVERAGED_METRICS,
    LOWER_IS_BETTER,
    METRICS_BY_SOURCE,
)
from app.modules.marketing.sources.gads import AdsNotConfigured, developer_token_scope

logger = logging.getLogger("schakl.marketing")

#: Impression/session-weighted, not summed, when aggregating a period (see module doc).
_WEIGHT_BY_METRIC = {"ctr": "impressions", "position": "impressions", "engagementRate": "sessions"}

#: The connect-flow query flag that adds each source's scope (the picker's connect deep-link).
_CONNECT_FLAG = {
    MarketingSource.GA4.value: "include_analytics",
    MarketingSource.GSC.value: "include_search_console",
    MarketingSource.GADS.value: "include_ads",
}

#: The headline metric each overview column reads, and which source feeds it (#133).
_OVERVIEW_COLUMNS: dict[str, tuple[str, str]] = {
    # column key: (source, metric)
    "sessions": (MarketingSource.GA4.value, "sessions"),
    "clicks": (MarketingSource.GSC.value, "clicks"),
    "position": (MarketingSource.GSC.value, "position"),
    "cost": (MarketingSource.GADS.value, "cost"),
    "conversions": (MarketingSource.GA4.value, "conversions"),
}

_DRILLDOWN_TTL = 3600  # ~1h, tier-2 lives behind this (issue #133)

#: The GA4 metrics a client's ``show_key_events=False`` withholds — key events and their
#: display alias. Scoped to GA4: Google Ads keeps its own ``conversions`` (#134).
_GA4_GATED_METRICS = ("keyEvents", "conversions")


def _delta_pct(current: float, previous: float) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def aggregate(source: str, rows: list[dict[str, Any]]) -> dict[str, float]:
    """Collapse a list of daily ``metrics`` dicts into one period total for ``source``."""
    out: dict[str, float] = {}
    for metric in METRICS_BY_SOURCE.get(source, []):
        if metric in AVERAGED_METRICS:
            weight_key = _WEIGHT_BY_METRIC.get(metric)
            num = 0.0
            den = 0.0
            for row in rows:
                value = float(row.get(metric, 0) or 0)
                weight = float(row.get(weight_key, 0) or 0) if weight_key else 1.0
                num += value * weight
                den += weight
            out[metric] = round(num / den, 4) if den else 0.0
        else:
            out[metric] = round(sum(float(row.get(metric, 0) or 0) for row in rows), 4)
    return out


async def resolve_ads_developer_token(session: AsyncSession, org_id: uuid.UUID) -> str | None:
    """The effective Google Ads developer token for ``org_id`` (#134).

    The org's stored (encrypted) token, else the legacy ``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env
    fallback, else ``None``. Callers bind it around the Ads adapter with
    :func:`developer_token_scope`; the request services and the worker sync share this one resolver
    so "where does the token come from" has exactly one answer.
    """
    row = await session.scalar(
        select(MarketingSettings).where(MarketingSettings.org_id == org_id)
    )
    if row is not None and row.ads_developer_token_encrypted:
        try:
            return decrypt(row.ads_developer_token_encrypted)
        except ValueError:  # key rotated: the stored token is dead — fall through to the env
            pass
    return settings.google_ads_developer_token or None


class MarketingService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _resolve_ads_developer_token(self) -> str | None:
        return await resolve_ads_developer_token(self.ctx.session, self.ctx.org.id)

    # --- shared helpers ------------------------------------------------------------------- #
    async def _today(self) -> date:
        zone = await org_zoneinfo(self.ctx.session, self.ctx.org.id)
        return datetime.now(zone).date()

    async def _company_or_404(self, company_id: uuid.UUID) -> Company:
        return await self.ctx.repo(Company).get_or_404(company_id)

    async def _links(
        self, *, company_id: uuid.UUID | None = None, include_inactive: bool = False
    ) -> list[MarketingLink]:
        stmt = select(MarketingLink).where(MarketingLink.org_id == self.ctx.org.id)
        if company_id is not None:
            stmt = stmt.where(MarketingLink.company_id == company_id)
        if not include_inactive:
            stmt = stmt.where(MarketingLink.active.is_(True))
        stmt = stmt.order_by(MarketingLink.source, MarketingLink.display_name)
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def _connections_by_id(self) -> dict[uuid.UUID, GoogleConnection]:
        rows = (
            (
                await self.ctx.session.execute(
                    select(GoogleConnection).where(GoogleConnection.org_id == self.ctx.org.id)
                )
            )
            .scalars()
            .all()
        )
        return {row.id: row for row in rows}

    async def _any_connection(self) -> bool:
        return bool(
            await self.ctx.session.scalar(
                select(func.count(GoogleConnection.id)).where(
                    GoogleConnection.org_id == self.ctx.org.id
                )
            )
        )

    async def _settings_map(
        self, company_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[bool, dict | None]]:
        """``{company_id: (show_key_events, layout)}`` for the given companies — one query.

        A company with **no** settings row falls back to the defaults (``True``, no layout):
        absence means the pre-existing behaviour, so nothing changes until someone edits.
        """
        if not company_ids:
            return {}
        rows = (
            await self.ctx.session.execute(
                select(
                    MarketingCompanySettings.company_id,
                    MarketingCompanySettings.show_key_events,
                    MarketingCompanySettings.layout,
                ).where(
                    MarketingCompanySettings.org_id == self.ctx.org.id,
                    MarketingCompanySettings.company_id.in_(company_ids),
                )
            )
        ).all()
        return {company_id: (bool(flag), layout) for company_id, flag, layout in rows}

    async def _company_settings(self, company_id: uuid.UUID) -> tuple[bool, dict | None]:
        return (await self._settings_map([company_id])).get(company_id, (True, None))

    async def _show_key_events(self, company_id: uuid.UUID) -> bool:
        return (await self._company_settings(company_id))[0]

    # --- links (#132) --------------------------------------------------------------------- #
    async def list_links_read(self, company_id: uuid.UUID) -> list[LinkRead]:
        self.ctx.require("marketing.metrics.read")
        # 404 on another tenant's (or a nonexistent) company, so the list can't be probed for the
        # existence of a company outside the caller's org; an own company with no links is [].
        await self._company_or_404(company_id)
        links = await self._links(company_id=company_id, include_inactive=True)
        connections = await self._connections_by_id()
        return [self._link_read(link, connections) for link in links]

    def _link_read(
        self, link: MarketingLink, connections: dict[uuid.UUID, GoogleConnection]
    ) -> LinkRead:
        connection = connections.get(link.connection_id) if link.connection_id else None
        return LinkRead(
            id=link.id,
            company_id=link.company_id,
            source=MarketingSource(link.source),
            external_id=link.external_id,
            display_name=link.display_name,
            config=link.config or {},
            active=link.active,
            last_synced_at=link.last_synced_at,
            last_error=link.last_error,
            backfill_done=link.backfill_done,
            connection_ok=bool(connection and connection.status == ConnectionStatus.ACTIVE.value),
        )

    async def create_link(self, data: LinkCreate) -> LinkRead:
        self.ctx.require("marketing.link.manage")
        await self._company_or_404(data.company_id)
        # The caller's own connection is what will sync this link (per-user OAuth); listing the
        # picker options already proved it exists and carries the scope.
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        existing = (
            await self.ctx.session.execute(
                select(MarketingLink).where(
                    MarketingLink.org_id == self.ctx.org.id,
                    MarketingLink.company_id == data.company_id,
                    MarketingLink.source == data.source.value,
                    MarketingLink.external_id == data.external_id,
                )
            )
        ).scalar_one_or_none()

        if existing is not None:
            reactivated = not existing.active
            existing.active = True
            existing.display_name = data.display_name
            existing.config = data.config
            if connection is not None:
                existing.connection_id = connection.id
            await self.ctx.session.flush()
            link = existing
        else:
            link = MarketingLink(
                org_id=self.ctx.org.id,
                company_id=data.company_id,
                source=data.source.value,
                external_id=data.external_id,
                display_name=data.display_name,
                config=data.config,
                connection_id=connection.id if connection else None,
                created_by_user_id=self.ctx.user.id,
            )
            self.ctx.session.add(link)
            await self.ctx.session.flush()
            reactivated = False

        await ActivityService(self.ctx).record(
            "company",
            data.company_id,
            "marketing.linked",
            {"source": link.source, "name": link.display_name},
        )
        # Kick off the 13-month backfill so sparklines/YoY work from day one — a one-off worker
        # job, deferred so the create's transaction has committed. A queue miss is not fatal.
        if not link.backfill_done and not reactivated:
            try:
                await enqueue(
                    "marketing_backfill_link",
                    str(self.ctx.org.id),
                    str(link.id),
                    _defer_by=5,
                    _job_id=f"marketing-backfill-{link.id}",
                )
            except Exception:
                logger.warning("could not enqueue marketing backfill for link %s", link.id)
        connections = {connection.id: connection} if connection else {}
        return self._link_read(link, connections)

    async def deactivate_link(self, link_id: uuid.UUID) -> None:
        self.ctx.require("marketing.link.manage")
        link = await self.ctx.repo(MarketingLink).get_or_404(link_id)
        if link.active:
            link.active = False
            await self.ctx.session.flush()
            await ActivityService(self.ctx).record(
                "company",
                link.company_id,
                "marketing.unlinked",
                {"source": link.source, "name": link.display_name},
            )

    # --- per-client settings (#134) ------------------------------------------------------- #
    async def set_company_settings(
        self,
        company_id: uuid.UUID,
        *,
        show_key_events: bool | None = None,
        layout: dict | None = None,
    ) -> CompanySettingsRead:
        """Per-client marketing preferences (upsert, one row per org+company).

        Gated on ``marketing.link.manage`` — it's configuration, like linking. Two writers,
        kept coherent during the expand release (#192):

        * ``layout`` replaces the stored layout wholesale after validation (``{"sources": {}}``
          clears it back to the defaults); the legacy boolean is rewritten to match the GA4
          tiles so pre-layout readers keep agreeing with what is actually visible.
        * ``show_key_events`` (the #134 toggle) keeps working: where a layout with GA4 tiles
          exists, it edits those tiles (the boolean alone would silently lose the fight).

        Changes land on the client's activity trail (§16), only when something actually flips.
        """
        self.ctx.require("marketing.link.manage")
        await self._company_or_404(company_id)
        row = await self.ctx.session.scalar(
            select(MarketingCompanySettings).where(
                MarketingCompanySettings.org_id == self.ctx.org.id,
                MarketingCompanySettings.company_id == company_id,
            )
        )
        if row is None:
            # Explicit True: the column default only applies at INSERT, and the coherence
            # math below must see the semantic default, not a pre-flush None.
            row = MarketingCompanySettings(
                org_id=self.ctx.org.id, company_id=company_id, show_key_events=True
            )
            self.ctx.session.add(row)
        previous_visible = "keyEvents" in resolved_tiles(
            "ga4", source_layout(row.layout, "ga4"), bool(row.show_key_events)
        )
        previous_layout = row.layout

        if layout is not None:
            parsed = CompanyLayout.model_validate(layout)
            validate_layout(parsed)
            stored = parsed.model_dump(exclude_none=True) if parsed.sources else None
            row.layout = stored

        if show_key_events is not None:
            row.show_key_events = show_key_events
            src = source_layout(row.layout, "ga4")
            if src is not None and src.tiles is not None:
                # The toggle edits the curated tiles (#192): add the key-event tiles back in
                # their default place, or take them (and the by-event drill-down) out.
                tiles = [t for t in src.tiles if t not in GA4_KEY_EVENT_TILES]
                if show_key_events:
                    tiles = [
                        m
                        for m in METRICS_BY_SOURCE["ga4"]
                        if m in tiles or m in GA4_KEY_EVENT_TILES
                    ] + [t for t in tiles if t not in METRICS_BY_SOURCE["ga4"]]
                src.tiles = tiles
                if not show_key_events and src.drilldowns is not None:
                    src.drilldowns = [
                        d for d in src.drilldowns if d != GA4_KEY_EVENT_DRILLDOWN
                    ]
                new_layout = dict(row.layout or {"sources": {}})
                new_sources = dict(new_layout.get("sources") or {})
                new_sources["ga4"] = src.model_dump(exclude_none=True)
                new_layout["sources"] = new_sources
                row.layout = new_layout
        # Keep the legacy boolean coherent with the layout for pre-#192 readers.
        row.show_key_events = "keyEvents" in resolved_tiles(
            "ga4", source_layout(row.layout, "ga4"), bool(row.show_key_events)
        )
        await self.ctx.session.flush()

        now_visible = row.show_key_events
        activity = ActivityService(self.ctx)
        if previous_visible != now_visible:
            await activity.record(
                "company",
                company_id,
                "marketing.key_events_enabled"
                if now_visible
                else "marketing.key_events_disabled",
                {},
            )
        if layout is not None and previous_layout != row.layout:
            await activity.record("company", company_id, "marketing.layout_changed", {})
        return CompanySettingsRead(
            company_id=company_id, show_key_events=row.show_key_events, layout=row.layout
        )

    # --- pickers (#132) ------------------------------------------------------------------- #
    async def available_accounts(self, source: MarketingSource) -> AccountsResponse:
        self.ctx.require("marketing.link.manage")
        adapter = source_for(source.value)
        flag = _CONNECT_FLAG[source.value]
        connection = await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )
        if connection is None:
            return AccountsResponse(source=source, connected=False, connect_flag=flag)
        has_scope = adapter.scope in set(connection.scopes or [])
        if not has_scope or connection.status != ConnectionStatus.ACTIVE.value:
            return AccountsResponse(
                source=source, connected=True, has_scope=has_scope, connect_flag=flag
            )

        # Google Ads needs a per-org developer token; with none the picker teaches "not configured"
        # instead of calling Google (#134). GA4/GSC pass through with token=None.
        ads_token: str | None = None
        if source == MarketingSource.GADS:
            ads_token = await self._resolve_ads_developer_token()
            if not ads_token:
                return AccountsResponse(
                    source=source, connected=True, has_scope=True, configured=False,
                    connect_flag=flag,
                )

        cache_key = f"schakl:marketing:accounts:{connection.id}:{source.value}"
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            options = [AvailableAccount(**item) for item in json.loads(cached)]
        else:
            try:
                with developer_token_scope(ads_token):
                    # Pool connection released during the live account listing
                    # (docs/PERFORMANCE.md).
                    async with (
                        google_client.acting_as(self.ctx.session, self.ctx.org, connection)
                        as gclient,
                        self.ctx.release_db(),
                    ):
                        fetched = await adapter.list_accounts(gclient)
            except AdsNotConfigured:
                return AccountsResponse(
                    source=source, connected=True, has_scope=True, configured=False,
                    connect_flag=flag,
                )
            except Exception as exc:  # noqa: BLE001 — a live fetch failure is a reconnect prompt
                if await google_client.is_oauth_error(exc):
                    await google_client.mark_connection_error(
                        self.ctx.session, self.ctx.org, connection, str(exc)
                    )
                    return AccountsResponse(
                        source=source, connected=True, has_scope=False, connect_flag=flag,
                        error="errors.google_connection_error",
                    )
                logger.warning("marketing accounts fetch failed for %s: %s", source.value, exc)
                return AccountsResponse(
                    source=source, connected=True, has_scope=True, connect_flag=flag,
                    error="marketing.accounts_error",
                )
            options = [
                AvailableAccount(
                    external_id=opt.external_id,
                    display_name=opt.display_name,
                    account_hint=opt.account_hint,
                    config=opt.config,
                )
                for opt in fetched
            ]
            await redis.set(
                cache_key,
                json.dumps([opt.model_dump() for opt in options]),
                ex=600,  # 10 min; the picker refreshes in the background on open (#132)
            )
        return AccountsResponse(
            source=source, connected=True, has_scope=True, accounts=options, connect_flag=flag
        )

    # --- metrics for the panel + tab (#133), stored data only ----------------------------- #
    async def company_marketing(self, company_id: uuid.UUID, range_days: int) -> CompanyMarketing:
        self.ctx.require("marketing.metrics.read")
        await self._company_or_404(company_id)
        range_days = max(1, min(range_days, 400))
        today = await self._today()
        cur_end = today - timedelta(days=1)
        cur_start = cur_end - timedelta(days=range_days - 1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=range_days - 1)

        show_key_events, layout = await self._company_settings(company_id)
        links = await self._links(company_id=company_id)
        connections = await self._connections_by_id()
        sources: list[SourceMetrics] = []
        if links:
            metrics_by_link = await self._metrics_for_links(
                [link.id for link in links], prev_start, cur_end
            )
            for link in links:
                sources.append(
                    self._source_metrics(
                        link,
                        metrics_by_link.get(link.id, {}),
                        connections,
                        cur_start,
                        cur_end,
                        prev_start,
                        prev_end,
                        show_key_events=show_key_events,
                        layout=layout,
                    )
                )
        can_manage = self.ctx.can("marketing.link.manage")
        return CompanyMarketing(
            company_id=company_id,
            range_days=range_days,
            sources=sources,
            needs_connection=not await self._any_connection(),
            can_manage=can_manage,
            show_key_events=show_key_events,
            # The stored layout feeds the tab's edit mode (#192) — manager-only, like the
            # settings write it configures.
            layout=layout if can_manage else None,
        )

    async def _metrics_for_links(
        self, link_ids: list[uuid.UUID], start: date, end: date
    ) -> dict[uuid.UUID, dict[date, dict[str, Any]]]:
        """One query for every link's daily rows in ``[start, end]`` → {link_id: {day: metrics}}."""
        if not link_ids:
            return {}
        rows = (
            
                await self.ctx.session.execute(
                    select(
                        MarketingMetricDaily.link_id,
                        MarketingMetricDaily.date,
                        MarketingMetricDaily.metrics,
                        MarketingMetricDaily.currency,
                    ).where(
                        MarketingMetricDaily.org_id == self.ctx.org.id,
                        MarketingMetricDaily.link_id.in_(link_ids),
                        MarketingMetricDaily.date >= start,
                        MarketingMetricDaily.date <= end,
                    )
                )
            
        ).all()
        out: dict[uuid.UUID, dict[date, dict[str, Any]]] = defaultdict(dict)
        for link_id, day, metrics, currency in rows:
            payload = dict(metrics or {})
            payload["_currency"] = currency
            out[link_id][day] = payload
        return out

    def _source_metrics(
        self,
        link: MarketingLink,
        daily: dict[date, dict[str, Any]],
        connections: dict[uuid.UUID, GoogleConnection],
        cur_start: date,
        cur_end: date,
        prev_start: date,
        prev_end: date,
        *,
        show_key_events: bool = True,
        layout: dict | None = None,
    ) -> SourceMetrics:
        adapter = source_for(link.source)
        current_rows = [m for day, m in daily.items() if cur_start <= day <= cur_end]
        prev_rows = [m for day, m in daily.items() if prev_start <= day <= prev_end]
        cur_agg = aggregate(link.source, current_rows)
        prev_agg = aggregate(link.source, prev_rows)
        # The client's curated layout (#192) — or, where none exists, the legacy key-events
        # gate (#134) — decides which tiles exist. Hidden tiles are dropped from the payload
        # entirely (never a client-side hide), so no consumer ever sees them.
        src_layout = source_layout(layout, link.source)
        metrics = resolved_tiles(link.source, src_layout, show_key_events)
        kpis = {
            metric: KpiValue(
                current=cur_agg.get(metric, 0.0),
                previous=prev_agg.get(metric, 0.0),
                delta_pct=_delta_pct(cur_agg.get(metric, 0.0), prev_agg.get(metric, 0.0)),
                lower_is_better=metric in LOWER_IS_BETTER,
            )
            for metric in metrics
        }

        # A gap-free daily series across the current window (0-fill), for sparkline/trend.
        span = (cur_end - cur_start).days + 1
        dates = [cur_start + timedelta(days=i) for i in range(span)]
        series_metrics: dict[str, list[float]] = {
            metric: [float(daily.get(day, {}).get(metric, 0) or 0) for day in dates]
            for metric in metrics
        }

        channels = None
        if link.source == MarketingSource.GA4.value:
            channels = defaultdict(float)
            for day in dates:
                for group, value in (daily.get(day, {}).get("channels", {}) or {}).items():
                    channels[group] += float(value or 0)
            channels = dict(channels)

        currency = next(
            (m.get("_currency") for m in current_rows if m.get("_currency")),
            (link.config or {}).get("currency"),
        )
        return SourceMetrics(
            link_id=link.id,
            source=MarketingSource(link.source),
            display_name=link.display_name,
            external_id=link.external_id,
            health=self._health(link, connections, bool(daily)),
            last_error=link.last_error,
            last_synced_at=link.last_synced_at,
            currency=currency,
            deep_link=adapter.deep_link(link.external_id, link.config or {}),
            primary_metric=resolved_primary(link.source, src_layout, metrics),
            kpis=kpis,
            series=SeriesData(dates=dates, metrics=series_metrics),
            channels=channels,
            tiles=metrics,
            tile_labels=(src_layout.labels if src_layout else {}),
            drilldowns=resolved_drilldowns(
                link.source, adapter.drilldowns, src_layout, metrics
            ),
        )

    def _health(
        self,
        link: MarketingLink,
        connections: dict[uuid.UUID, GoogleConnection],
        has_data: bool,
    ) -> str:
        connection = connections.get(link.connection_id) if link.connection_id else None
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
            return "disconnected"
        if link.last_error:
            return "error"
        if not link.backfill_done and not has_data:
            return "pending"
        return "ok" if has_data else "pending"

    # --- drill-downs (#133), live behind a Redis TTL -------------------------------------- #
    async def drilldown(
        self, company_id: uuid.UUID, link_id: uuid.UUID, kind: str, range_days: int
    ) -> DrilldownResponse:
        self.ctx.require("marketing.metrics.read")
        link = await self.ctx.repo(MarketingLink).get_or_404(link_id)
        if link.company_id != company_id:
            raise AppError("not_found", "errors.not_found", status_code=404)
        adapter = source_for(link.source)
        if kind not in adapter.drilldowns:
            raise AppError("validation", "errors.validation", status_code=422)
        # The client's layout decides which drill-downs exist (#192) — including the legacy
        # key-events gate (#134): a hidden keyEvents tile takes its breakdown with it.
        show_key_events, layout = await self._company_settings(company_id)
        src_layout = source_layout(layout, link.source)
        tiles = resolved_tiles(link.source, src_layout, show_key_events)
        if kind not in resolved_drilldowns(link.source, adapter.drilldowns, src_layout, tiles):
            raise AppError("validation", "errors.validation", status_code=422)
        range_days = max(1, min(range_days, 400))
        today = await self._today()
        end = today - timedelta(days=1)
        start = end - timedelta(days=range_days - 1)
        deep_link = adapter.deep_link(link.external_id, link.config or {})
        source = MarketingSource(link.source)

        connection = (
            await self.ctx.session.get(GoogleConnection, link.connection_id)
            if link.connection_id
            else None
        )
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
            return DrilldownResponse(
                source=source, kind=kind, available=False,
                unavailable_reason="marketing.disconnected", deep_link=deep_link,
            )

        redis = get_redis()
        cache_key = f"schakl:marketing:drill:{link.id}:{kind}:{range_days}"
        cached = await redis.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            return DrilldownResponse(
                source=source, kind=kind, columns=payload["columns"],
                rows=[DrilldownRowOut(**row) for row in payload["rows"]], deep_link=deep_link,
            )
        ads_token = (
            await self._resolve_ads_developer_token()
            if source == MarketingSource.GADS
            else None
        )
        try:
            with developer_token_scope(ads_token):
                # The live Google fetch runs with the pool connection released — a cold
                # drill-down takes seconds, and six of them fire per tab (docs/PERFORMANCE.md).
                async with (
                    google_client.acting_as(self.ctx.session, self.ctx.org, connection)
                    as gclient,
                    self.ctx.release_db(),
                ):
                    table = await adapter.drilldown(
                        gclient, link.external_id, kind, start, end, link.config or {}
                    )
        except AdsNotConfigured:
            return DrilldownResponse(
                source=source, kind=kind, available=False,
                unavailable_reason="marketing.ads_not_configured", deep_link=deep_link,
            )
        except Exception as exc:  # noqa: BLE001
            if await google_client.is_oauth_error(exc):
                await google_client.mark_connection_error(
                    self.ctx.session, self.ctx.org, connection, str(exc)
                )
                reason = "marketing.disconnected"
            else:
                logger.warning("marketing drilldown failed (%s/%s): %s", link.source, kind, exc)
                reason = "marketing.accounts_error"
            return DrilldownResponse(
                source=source, kind=kind, available=False, unavailable_reason=reason,
                deep_link=deep_link,
            )
        rows = [
            DrilldownRowOut(label=row.label, href=row.href, metrics=row.metrics)
            for row in table.rows
        ]
        await redis.set(
            cache_key,
            json.dumps({"columns": table.columns, "rows": [r.model_dump() for r in rows]}),
            ex=_DRILLDOWN_TTL,
        )
        return DrilldownResponse(
            source=source, kind=kind, columns=table.columns, rows=rows, deep_link=deep_link
        )

    # --- cross-client overview (#133), stored data only ----------------------------------- #
    async def overview(self, range_days: int, sort: str | None) -> OverviewResponse:
        self.ctx.require("marketing.report.read")
        range_days = max(1, min(range_days, 400))
        today = await self._today()
        cur_end = today - timedelta(days=1)
        cur_start = cur_end - timedelta(days=range_days - 1)
        prev_end = cur_start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=range_days - 1)

        pairs = (
            await self.ctx.session.execute(
                select(MarketingLink, Company.name)
                .join(Company, Company.id == MarketingLink.company_id)
                .where(
                    MarketingLink.org_id == self.ctx.org.id, MarketingLink.active.is_(True)
                )
            )
        ).all()
        if not pairs:
            return OverviewResponse(range_days=range_days, rows=[], total=0)

        links = [pair[0] for pair in pairs]
        names = {pair[0].company_id: pair[1] for pair in pairs}
        metrics_by_link = await self._metrics_for_links(
            [link.id for link in links], prev_start, cur_end
        )

        # company -> source -> (current rows, previous rows)
        by_company: dict[uuid.UUID, dict[str, tuple[list, list]]] = defaultdict(
            lambda: defaultdict(lambda: ([], []))
        )
        sources_present: dict[uuid.UUID, set[str]] = defaultdict(set)
        for link in links:
            sources_present[link.company_id].add(link.source)
            daily = metrics_by_link.get(link.id, {})
            cur, prev = by_company[link.company_id][link.source]
            for day, m in daily.items():
                (cur if cur_start <= day <= cur_end else prev).append(m)

        settings = await self._settings_map(list(by_company.keys()))
        rows: list[OverviewRow] = []
        for company_id, per_source in by_company.items():
            show_key_events, layout = settings.get(company_id, (True, None))
            agg_cur = {s: aggregate(s, buckets[0]) for s, buckets in per_source.items()}
            agg_prev = {s: aggregate(s, buckets[1]) for s, buckets in per_source.items()}
            # Which metric keys this client's layout leaves visible, per source (#192) — the
            # grid respects per-company hidden metrics exactly like the panel/tab.
            visible = {
                s: set(resolved_tiles(s, source_layout(layout, s), show_key_events))
                for s in per_source
            }
            metrics: dict[str, KpiValue] = {}
            for col, (src, metric) in _OVERVIEW_COLUMNS.items():
                if src not in per_source:
                    continue
                # A hidden tile shows no number here either, matching the panel/tab (#134/#192).
                if metric not in visible.get(src, set()):
                    continue
                cur_v = agg_cur[src].get(metric, 0.0)
                prev_v = agg_prev[src].get(metric, 0.0)
                metrics[col] = KpiValue(
                    current=cur_v,
                    previous=prev_v,
                    delta_pct=_delta_pct(cur_v, prev_v),
                    lower_is_better=metric in LOWER_IS_BETTER,
                )
            present = sorted(sources_present[company_id])
            # The grid's toggle reflects the *effective* visibility: for a layout-curated
            # client the tiles decide, not the legacy boolean (#192).
            key_events_visible = (
                "keyEvents" in visible["ga4"] if "ga4" in visible else show_key_events
            )
            rows.append(
                OverviewRow(
                    company_id=company_id,
                    company_name=names.get(company_id, ""),
                    sources_present=[MarketingSource(s) for s in present],
                    metrics=metrics,
                    show_key_events=key_events_visible,
                )
            )
        rows = self._sort_overview(rows, sort)
        return OverviewResponse(range_days=range_days, rows=rows, total=len(rows))

    def _sort_overview(self, rows: list[OverviewRow], sort: str | None) -> list[OverviewRow]:
        key = (sort or "company_name").lstrip("-")
        descending = bool(sort and sort.startswith("-"))
        if key == "company_name":
            return sorted(rows, key=lambda r: r.company_name.lower(), reverse=descending)
        if key not in _OVERVIEW_COLUMNS:
            return sorted(rows, key=lambda r: r.company_name.lower())
        # Rows missing the metric sort last regardless of direction (they have no number).
        def metric_key(row: OverviewRow) -> tuple[int, float]:
            kpi = row.metrics.get(key)
            return (0, kpi.current) if kpi is not None else (1, 0.0)

        present = [r for r in rows if key in r.metrics]
        absent = [r for r in rows if key not in r.metrics]
        present.sort(key=lambda r: r.metrics[key].current, reverse=descending)
        return present + sorted(absent, key=lambda r: r.company_name.lower())


class MarketingSettingsService:
    """Org-level marketing settings (#134): the encrypted Google Ads developer token.

    Mirrors ``GoogleSettingsService`` — the token is write-only (an empty value keeps the stored
    one) and the read reports only whether one is configured, never the value itself.
    """

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    async def _row(self) -> MarketingSettings | None:
        return await self.ctx.session.scalar(
            select(MarketingSettings).where(MarketingSettings.org_id == self.ctx.org.id)
        )

    def _read(self, row: MarketingSettings | None) -> MarketingSettingsRead:
        return MarketingSettingsRead(
            ads_developer_token_configured=bool(row and row.ads_developer_token_encrypted),
            env_ads_token_configured=bool(settings.google_ads_developer_token),
        )

    async def get(self) -> MarketingSettingsRead:
        return self._read(await self._row())

    async def save(self, data: MarketingSettingsWrite) -> MarketingSettingsRead:
        self.ctx.require("marketing.link.manage")
        row = await self._row()
        # An empty token keeps the stored one; a resent identical token is not a change (Fernet
        # would otherwise re-encrypt on every save) — the Google-client-secret rule.
        token_encrypted = row.ads_developer_token_encrypted if row else None
        if data.ads_developer_token:
            stored_plain: str | None = None
            if token_encrypted:
                try:
                    stored_plain = decrypt(token_encrypted)
                except ValueError:  # rotated key: the stored token is dead anyway
                    stored_plain = None
            if stored_plain != data.ads_developer_token:
                token_encrypted = encrypt(data.ads_developer_token)
        if row is None:
            row = MarketingSettings(
                org_id=self.ctx.org.id, ads_developer_token_encrypted=token_encrypted
            )
            self.ctx.session.add(row)
        else:
            row.ads_developer_token_encrypted = token_encrypted
        await self.ctx.session.flush()
        return self._read(row)


# --- sync (worker side, no request) ---------------------------------------------------------- #
async def sync_link_range(
    session: Any, org: Any, link: MarketingLink, start: date, end: date
) -> None:
    """Fetch ``[start, end]`` for one link and idempotently upsert its daily rows (#133).

    Runs in a worker transaction with the RLS GUC already bound to ``org``. A dead grant flips
    the connection to error and stops *this* link; a plain API error records ``last_error`` on the
    link but never raises, so one broken link never stops the others' sync.
    """
    adapter = source_for(link.source)
    if link.connection_id is None:
        link.last_error = "errors.google_not_connected"
        return
    connection = await session.get(GoogleConnection, link.connection_id)
    if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
        link.last_error = "errors.google_connection_error"
        return
    if adapter.scope not in set(connection.scopes or []):
        link.last_error = "errors.google_connection_error"
        return
    ads_token = (
        await resolve_ads_developer_token(session, org.id)
        if link.source == MarketingSource.GADS.value
        else None
    )
    try:
        with developer_token_scope(ads_token):
            async with google_client.acting_as(session, org, connection) as gclient:
                daily = await adapter.fetch_daily(
                    gclient, link.external_id, start, end, link.config or {}
                )
    except AdsNotConfigured:
        link.last_error = "marketing.ads_not_configured"
        return
    except Exception as exc:  # noqa: BLE001
        if await google_client.is_oauth_error(exc):
            await google_client.mark_connection_error(session, org, connection, str(exc))
            link.last_error = "errors.google_connection_error"
            return
        logger.warning("marketing sync failed for link %s: %s", link.id, exc)
        link.last_error = str(exc)[:500]
        return

    await _upsert_daily(session, link, daily)
    link.last_error = None
    link.last_synced_at = datetime.now(UTC)


async def _upsert_daily(session: Any, link: MarketingLink, daily: list) -> None:
    """Idempotent per-day upsert keyed on (org_id, link_id, date)."""
    if not daily:
        return
    days = [d.day for d in daily]
    existing = {
        row.date: row
        for row in (
            await session.execute(
                select(MarketingMetricDaily).where(
                    MarketingMetricDaily.org_id == link.org_id,
                    MarketingMetricDaily.link_id == link.id,
                    MarketingMetricDaily.date.in_(days),
                )
            )
        )
        .scalars()
        .all()
    }
    now = datetime.now(UTC)
    for point in daily:
        row = existing.get(point.day)
        if row is None:
            session.add(
                MarketingMetricDaily(
                    org_id=link.org_id,
                    link_id=link.id,
                    date=point.day,
                    metrics=point.metrics,
                    currency=point.currency,
                    synced_at=now,
                )
            )
        else:
            row.metrics = point.metrics
            row.currency = point.currency
            row.synced_at = now
    await session.flush()
