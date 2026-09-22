"""The leads dashboard read path, and the editor's catalog.

One request: resolve the client's profile and links, resolve the period on the org's calendar,
refuse a filter the profile cannot express, fetch (or take from Redis) the GA4 batch and the
three Ads queries, compute the widgets, and say what could not be answered and why.

Two rules about the fetch. **The cache holds Google's raw answers, keyed on the exact requests
sent**: a relabel in the profile shows immediately (labels are applied on the way out), and a
change to a role re-fetches because the request changed. And **every Google round-trip runs
with the pool connection released** (``ctx.release_db()``, docs/PERFORMANCE.md): a cold
dashboard is two GA4 batches and three Ads queries, seconds of somebody else's latency.

A client-facing login gets the widgets, the coverage and the note — and never the deep links,
the unavailable list or the diagnostics about the agency's own setup (§15, #274).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from app.config import settings
from app.core.cache import get_redis
from app.core.googleads import AdsCredentials, AdsNotConfigured, ads_client, normalise_customer_id
from app.core.periods import range_token, resolve_period
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.errors import AppError
from app.i18n import resolve_locale
from app.integrations.google import client as google_client
from app.integrations.google.models import ConnectionStatus, GoogleConnection
from app.modules.companies.models import Company
from app.modules.marketing.leads import ga4
from app.modules.marketing.leads.ads import AdsData, compute_ads
from app.modules.marketing.leads.ads import queries as ads_queries
from app.modules.marketing.leads.profile import (
    ROLE_REQUEST,
    LeadProfile,
    parse_profile,
    resolve_channel_groups,
)
from app.modules.marketing.leads.schemas import (
    CatalogAction,
    CatalogDimension,
    CatalogEvent,
    LeadBreakpoint,
    LeadFilter,
    LeadFilterOption,
    LeadsCatalog,
    LeadsDashboard,
    LeadsWindow,
    LeadWarning,
)
from app.modules.marketing.leads.widgets import compute, period_values
from app.modules.marketing.models import (
    MarketingCompanySettings,
    MarketingLink,
    MarketingSettings,
    MarketingSource,
)
from app.modules.marketing.service import (
    MAX_RANGE_DAYS,
    _failure_key,
    resolve_ads_developer_token,
)

logger = logging.getLogger("schakl.marketing.leads")

#: How long a fetched dashboard is served from Redis. **A day**, because the answer is a day's
#: answer: every span ends yesterday at the latest (``resolve_period``), so the requests — and
#: with them the key — change at the org's midnight, and GA4 itself finalises a day well after it
#: ends. An hour was the first guess, and it meant the first person each hour paid Google's
#: latency on every view they opened, filter and period tab included. The nightly warm
#: (``jobs.marketing_warm_leads``) reads the day's keys before anyone is at their desk.
CACHE_TTL = 86400
#: Staff-only diagnostics: about the agency's setup, never the client's business.
_STAFF_WARNINGS = frozenset(
    {"silent_zero", "dimension_unregistered", "key_events_mismatch", "report_failed"}
)

# Test seam for the GA4 half — the Ads half rides ``app.core.googleads.set_transport``.
_transport: httpx.AsyncBaseTransport | None = None


def set_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    global _transport
    _transport = transport


UserFilters = dict[str, list[str]]


def parse_filters(raw: list[str]) -> UserFilters:
    """``["service:autotransport", "language:nl"]`` → ``{"service": ["autotransport"], …}``.

    A clause with no colon is refused, never dropped: a filter silently ignored answers a
    different question with every row still valid.
    """
    out: UserFilters = {}
    for clause in raw:
        key, sep, value = clause.partition(":")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"f": "errors.marketing_lead_filter_invalid"},
                details={"clause": clause[:100]},
            )
        out.setdefault(key, [])
        if value not in out[key]:
            out[key].append(value)
    return out


class LeadsService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # --- what the profile and the org say ------------------------------------------------ #
    async def _settings(self, company_id: uuid.UUID) -> tuple[LeadProfile | None, dict | None]:
        row = (
            await self.ctx.session.execute(
                select(
                    select(MarketingCompanySettings.lead_profile)
                    .where(
                        MarketingCompanySettings.org_id == self.ctx.org.id,
                        MarketingCompanySettings.company_id == company_id,
                    )
                    .scalar_subquery(),
                    select(MarketingSettings.channel_groups)
                    .where(MarketingSettings.org_id == self.ctx.org.id)
                    .scalar_subquery(),
                )
            )
        ).one()
        return parse_profile(row[0]), row[1]

    async def _link(
        self, company_id: uuid.UUID, source: MarketingSource, link_id: uuid.UUID | None
    ) -> MarketingLink | None:
        stmt = (
            self.ctx.repo(MarketingLink)
            .scoped_select()
            .where(
                MarketingLink.company_id == company_id,
                MarketingLink.source == source.value,
                MarketingLink.active.is_(True),
            )
            .order_by(MarketingLink.created_at)
        )
        links = list((await self.ctx.session.execute(stmt)).scalars())
        if link_id is not None:
            return next((link for link in links if link.id == link_id), None)
        return links[0] if links else None

    async def _connection(self, link: MarketingLink) -> GoogleConnection | None:
        if not link.connection_id:
            return None
        connection = await self.ctx.session.get(GoogleConnection, link.connection_id)
        if connection is None or connection.status != ConnectionStatus.ACTIVE.value:
            return None
        return connection

    def _locale(self) -> str:
        return resolve_locale(self.ctx.user.locale)

    # --- the dashboard --------------------------------------------------------------------- #
    async def dashboard(
        self, company_id: uuid.UUID, period: str | None, filters: UserFilters
    ) -> LeadsDashboard:
        self.ctx.require("marketing.metrics.read")
        await self.ctx.repo(Company).get_or_404(company_id)
        can_manage = self.ctx.can("marketing.link.manage")
        portal = self.ctx.is_portal
        profile, org_groups = await self._settings(company_id)
        if profile is None:
            return LeadsDashboard(company_id=company_id, configured=False, can_manage=can_manage)

        filter_dimensions = profile.filter_dimensions()
        for key in filters:
            if key not in filter_dimensions:
                raise AppError(
                    "validation",
                    "errors.validation",
                    status_code=422,
                    fields={"f": "errors.marketing_lead_filter_unknown"},
                    details={"dimension": key, "dimensions": filter_dimensions},
                )

        today = await org_today(self.ctx.session, self.ctx.org.id)
        start, end = resolve_period(period, today, max_days=MAX_RANGE_DAYS)
        locale = self._locale()
        hard = profile.hard_breakpoint()
        window = LeadsWindow(
            start=start,
            end=end,
            token=range_token(start, end),
            comparable_from=hard.date if hard else None,
        )
        channel_groups = resolve_channel_groups(org_groups, profile)

        ga4_link = await self._link(company_id, MarketingSource.GA4, profile.ga4_link_id)
        gads_link = (
            await self._link(company_id, MarketingSource.GADS, profile.gads_link_id)
            if profile.ads.enabled
            else None
        )
        warnings: list[LeadWarning] = []
        if hard and start < hard.date:
            warnings.append(
                LeadWarning(
                    code="before_breakpoint",
                    details={"date": hard.date.isoformat(), "text": hard.text(locale)},
                )
            )

        refreshed: list[datetime] = []
        widgets = []
        unavailable = []
        coverage = []
        seen: dict[str, dict[str, float]] = {}
        ga4_ok = False
        ga4_deep_link = ""
        if ga4_link is None:
            warnings.append(LeadWarning(code="ga4_not_linked", severity="error"))
        else:
            reports, fetched_at, failure = await self._ga4_reports(
                ga4_link, profile, filters, start, end
            )
            if failure:
                warnings.append(
                    LeadWarning(
                        code="ga4_unavailable", severity="error", details={"reason": failure}
                    )
                )
            else:
                ga4_ok = True
                property_number = ga4_link.external_id.rsplit("/", 1)[-1]
                ga4_deep_link = (
                    "https://analytics.google.com/analytics/web/#/p"
                    f"{property_number}/reports/intelligenthome"
                )
                if fetched_at:
                    refreshed.append(fetched_at)
                computed = compute(
                    profile,
                    reports,
                    start=start,
                    end=end,
                    locale=locale,
                    channel_groups=channel_groups,
                    has_ads=gads_link is not None,
                    filtered=bool(filters),
                )
                widgets.extend(computed.widgets)
                unavailable.extend(computed.unavailable)
                warnings.extend(computed.warnings)
                coverage = computed.coverage
                seen = computed.seen_values
                if filters:
                    # What a filter *offers* is what the period saw, not what the narrowed
                    # reports still contain — or picking one service removes every other
                    # from the control. The unfiltered plan is the view the reader clicked
                    # from, so this is a cache hit in every ordinary case; a miss costs the
                    # same two batches that view would have, and a failure costs only the
                    # options (the narrowed ones stand).
                    base, _at, base_failure = await self._ga4_reports(
                        ga4_link, profile, {}, start, end
                    )
                    if not base_failure:
                        seen = period_values(
                            profile, base, start=start, end=end, locale=locale
                        )
                if not portal:
                    warnings.extend(await self._setup_checks(ga4_link, profile))

        ads_ok = False
        ads_deep_link = ""
        if gads_link is not None:
            data, fetched_at, failure = await self._ads_data(gads_link, start, end)
            if failure:
                warnings.append(
                    LeadWarning(
                        code="ads_unavailable", severity="error", details={"reason": failure}
                    )
                )
            else:
                ads_ok = True
                ads_deep_link = f"https://ads.google.com/aw/overview?__c={gads_link.external_id}"
                if fetched_at:
                    refreshed.append(fetched_at)
                ads_widgets, ads_unavailable = compute_ads(
                    profile, data, start=start, end=end, locale=locale
                )
                widgets.extend(ads_widgets)
                unavailable.extend(ads_unavailable)
                for query, reason in data.failed.items():
                    warnings.append(
                        LeadWarning(
                            code="report_failed", details={"report": query, "reason": reason}
                        )
                    )

        filter_controls = [
            LeadFilter(
                dimension=key,
                title=spec.title(locale),
                options=sorted(
                    [
                        LeadFilterOption(key=raw, label=spec.value_label(raw, locale), count=count)
                        for raw, count in {
                            **{v: 0.0 for v in filters.get(key, [])},
                            **seen.get(key, {}),
                        }.items()
                    ],
                    key=lambda o: (-o.count, o.label),
                ),
                active=list(filters.get(key, [])),
            )
            for key, spec in profile.dimensions.items()
            if key in filter_dimensions and (key in seen or key in filters)
        ]

        if portal:
            warnings = [w for w in warnings if w.code not in _STAFF_WARNINGS]
            unavailable = []
            ga4_deep_link = ads_deep_link = ""

        return LeadsDashboard(
            company_id=company_id,
            configured=True,
            can_manage=can_manage,
            window=window,
            widgets=widgets,
            unavailable=unavailable,
            warnings=warnings,
            coverage=coverage,
            breakpoints=[
                LeadBreakpoint(date=b.date, text=b.text(locale), severity=b.severity)
                for b in profile.breakpoints
            ],
            filters=filter_controls,
            disclaimer=profile.disclaimer_text(locale),
            refreshed_at=min(refreshed) if refreshed else None,
            ga4_available=ga4_ok,
            ads_available=ads_ok,
            ga4_deep_link=ga4_deep_link if can_manage else "",
            ads_deep_link=ads_deep_link if can_manage else "",
        )

    # --- GA4 -------------------------------------------------------------------------------- #
    @asynccontextmanager
    async def _ga4_client(self, connection: GoogleConnection):  # noqa: ANN202
        async with (
            google_client.acting_as(
                self.ctx.session, self.ctx.org, connection, transport=_transport
            ) as client,
            self.ctx.release_db(),
        ):
            yield client

    async def _ga4_reports(
        self,
        link: MarketingLink,
        profile: LeadProfile,
        filters: UserFilters,
        start,  # noqa: ANN001
        end,  # noqa: ANN001
    ) -> tuple[dict[str, ga4.ParsedReport], datetime | None, str | None]:
        plan = ga4.build_plan(profile, filters)
        if not plan:
            return {}, None, None
        batches = ga4.batches(plan, start, end)
        digest = hashlib.sha256(json.dumps(batches, sort_keys=True).encode()).hexdigest()[:24]
        cache_key = f"schakl:marketing:leads:ga4:{link.id}:{digest}"
        redis = get_redis()
        cached = await redis.get(cache_key)
        bodies: list[dict[str, Any]]
        fetched_at: datetime | None
        if cached is not None:
            payload = json.loads(cached)
            bodies = payload["reports"]
            fetched_at = datetime.fromisoformat(payload["fetched_at"])
        else:
            connection = await self._connection(link)
            if connection is None:
                return {}, None, "marketing.disconnected"
            base = settings.google_analytics_data_base_url.rstrip("/")
            bodies = []
            try:
                async with self._ga4_client(connection) as client:
                    for batch in batches:
                        resp = await client.post(
                            f"{base}/{link.external_id}:batchRunReports",
                            json={"requests": batch},
                        )
                        resp.raise_for_status()
                        answered = resp.json().get("reports") or []
                        # Google answers one report per request, in order; a short answer is
                        # read as failures for the missing tail rather than shifted left.
                        answered += [{"error": {"status": "MISSING"}}] * (
                            len(batch) - len(answered)
                        )
                        bodies.extend(answered)
            except Exception as exc:  # noqa: BLE001
                if await google_client.is_oauth_error(exc):
                    await google_client.mark_connection_error(
                        self.ctx.session, self.ctx.org, connection, str(exc)
                    )
                    return {}, None, "marketing.disconnected"
                detail = google_client.describe_api_error(exc)
                logger.warning("leads dashboard GA4 fetch failed (%s): %s", link.id, detail or exc)
                return {}, None, _failure_key(detail, "marketing.accounts_error", source="ga4")
            fetched_at = datetime.now(UTC)
            await redis.set(
                cache_key,
                json.dumps({"fetched_at": fetched_at.isoformat(), "reports": bodies}),
                ex=CACHE_TTL,
            )
        reports = {
            spec.key: ga4.parse_report(spec.key, body)
            for spec, body in zip(plan, bodies, strict=False)
        }
        return reports, fetched_at, None

    async def _setup_checks(self, link: MarketingLink, profile: LeadProfile) -> list[LeadWarning]:
        """Whether the property carries what the profile assumes: every custom dimension the
        profile names is registered, and the property's key events are the request role.

        Two Admin reads, cached a day — configuration changes rarely and the answer is the
        same for every reader. A failure here costs the checks, never the dashboard."""
        redis = get_redis()
        cache_key = f"schakl:marketing:leads:setup:{link.id}"
        cached = await redis.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
        else:
            connection = await self._connection(link)
            if connection is None:
                return []
            base = settings.google_analytics_admin_base_url.rstrip("/")
            try:
                async with self._ga4_client(connection) as client:
                    dims = await client.get(
                        f"{base}/{link.external_id}/customDimensions", params={"pageSize": 200}
                    )
                    dims.raise_for_status()
                    keys = await client.get(
                        f"{base}/{link.external_id}/keyEvents", params={"pageSize": 200}
                    )
                    keys.raise_for_status()
            except Exception as exc:  # noqa: BLE001
                logger.info("leads setup check skipped (%s): %s", link.id, exc)
                return []
            payload = {
                "parameters": [
                    d.get("parameterName", "") for d in dims.json().get("customDimensions", [])
                ],
                "key_events": [k.get("eventName", "") for k in keys.json().get("keyEvents", [])],
            }
            await redis.set(cache_key, json.dumps(payload), ex=86400)
        warnings: list[LeadWarning] = []
        registered = set(payload.get("parameters") or [])
        for key, spec in profile.dimensions.items():
            if (
                spec.field.startswith("customEvent:")
                and spec.field.split(":", 1)[1] not in registered
            ):
                warnings.append(
                    LeadWarning(
                        code="dimension_unregistered",
                        details={"dimension": key, "field": spec.field},
                    )
                )
        key_events = set(payload.get("key_events") or [])
        request_exact = {m.value for m in profile.roles.get(ROLE_REQUEST, []) if m.match == "exact"}
        if request_exact and key_events and not (request_exact & key_events):
            warnings.append(
                LeadWarning(
                    code="key_events_mismatch",
                    details={"key_events": ", ".join(sorted(key_events))[:200]},
                )
            )
        return warnings

    # --- Ads -------------------------------------------------------------------------------- #
    async def _ads_data(
        self,
        link: MarketingLink,
        start,
        end,  # noqa: ANN001
    ) -> tuple[AdsData, datetime | None, str | None]:
        gaql = ads_queries(start, end)
        digest = hashlib.sha256(json.dumps(gaql, sort_keys=True).encode()).hexdigest()[:24]
        cache_key = f"schakl:marketing:leads:ads:{link.id}:{digest}"
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached is not None:
            payload = json.loads(cached)
            data = AdsData(**payload["data"])
            return data, datetime.fromisoformat(payload["fetched_at"]), None
        connection = await self._connection(link)
        if connection is None:
            return AdsData(), None, "marketing.disconnected"
        token = await resolve_ads_developer_token(self.ctx.session, self.ctx.org.id, ctx=self.ctx)
        if not token:
            return AdsData(), None, "marketing.ads_not_configured"
        config = link.config or {}
        credentials = AdsCredentials(
            developer_token=token,
            login_customer_id=normalise_customer_id(config.get("manager_id")) or None,
        )
        data = AdsData(currency=config.get("currency"))
        try:
            async with (
                ads_client(
                    self.ctx.session, self.ctx.org, connection, credentials, tool="leads"
                ) as client,
                self.ctx.release_db(),
            ):
                for key, query in gaql.items():
                    try:
                        rows = await client.search(
                            link.external_id, query, max_rows=5000, context=key
                        )
                    except AdsNotConfigured:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        if await google_client.is_oauth_error(exc):
                            raise
                        # One query failing costs its own widgets, not the whole half (§10's
                        # "several questions, one try" rule).
                        logger.warning(
                            "leads dashboard Ads query %s failed (%s): %s", key, link.id, exc
                        )
                        data.failed[key] = type(exc).__name__
                        continue
                    setattr(data, key.removeprefix("ads_"), rows)
        except AdsNotConfigured:
            return AdsData(), None, "marketing.ads_not_configured"
        except Exception as exc:  # noqa: BLE001
            if await google_client.is_oauth_error(exc):
                await google_client.mark_connection_error(
                    self.ctx.session, self.ctx.org, connection, str(exc)
                )
                return AdsData(), None, "marketing.disconnected"
            detail = google_client.describe_api_error(exc)
            logger.warning("leads dashboard Ads fetch failed (%s): %s", link.id, detail or exc)
            return AdsData(), None, _failure_key(detail, "marketing.accounts_error", source="gads")
        fetched_at = datetime.now(UTC)
        await redis.set(
            cache_key,
            json.dumps(
                {
                    "fetched_at": fetched_at.isoformat(),
                    "data": {
                        "daily": data.daily,
                        "campaigns": data.campaigns,
                        "actions": data.actions,
                        "currency": data.currency,
                        "failed": data.failed,
                    },
                }
            ),
            ex=CACHE_TTL,
        )
        return data, fetched_at, None

    # --- the editor's catalog -------------------------------------------------------------- #
    async def catalog(self, company_id: uuid.UUID) -> LeadsCatalog:
        """What this client's property and account actually carry, so the profile editor offers
        names to pick rather than a box to type them into. Manager-only, live, cached an hour."""
        self.ctx.require("marketing.link.manage")
        await self.ctx.repo(Company).get_or_404(company_id)
        profile, _ = await self._settings(company_id)
        ga4_link = await self._link(
            company_id, MarketingSource.GA4, profile.ga4_link_id if profile else None
        )
        gads_link = await self._link(
            company_id, MarketingSource.GADS, profile.gads_link_id if profile else None
        )
        out = LeadsCatalog()
        redis = get_redis()
        if ga4_link is not None:
            cache_key = f"schakl:marketing:leads:catalog:ga4:{ga4_link.id}"
            cached = await redis.get(cache_key)
            payload = json.loads(cached) if cached is not None else None
            if payload is None:
                connection = await self._connection(ga4_link)
                if connection is None:
                    out.unavailable_reason = "marketing.disconnected"
                else:
                    today = await org_today(self.ctx.session, self.ctx.org.id)
                    start, end = resolve_period("90d", today)
                    data_base = settings.google_analytics_data_base_url.rstrip("/")
                    admin_base = settings.google_analytics_admin_base_url.rstrip("/")
                    try:
                        async with self._ga4_client(connection) as client:
                            events = await client.post(
                                f"{data_base}/{ga4_link.external_id}:runReport",
                                json=ga4.ReportSpec(
                                    key="catalog", dimensions=(ga4.EVENT_NAME,), limit=250
                                ).request(start, end),
                            )
                            events.raise_for_status()
                            channels = await client.post(
                                f"{data_base}/{ga4_link.external_id}:runReport",
                                json=ga4.ReportSpec(
                                    key="channels",
                                    dimensions=(ga4.CHANNEL,),
                                    metrics=(ga4.SESSIONS,),
                                    limit=50,
                                    order_metric=ga4.SESSIONS,
                                ).request(start, end),
                            )
                            channels.raise_for_status()
                            dims = await client.get(
                                f"{admin_base}/{ga4_link.external_id}/customDimensions",
                                params={"pageSize": 200},
                            )
                            dims.raise_for_status()
                            keys = await client.get(
                                f"{admin_base}/{ga4_link.external_id}/keyEvents",
                                params={"pageSize": 200},
                            )
                            keys.raise_for_status()
                    except Exception as exc:  # noqa: BLE001
                        detail = google_client.describe_api_error(exc)
                        logger.warning(
                            "leads catalog GA4 read failed (%s): %s", ga4_link.id, detail or exc
                        )
                        out.unavailable_reason = _failure_key(
                            detail, "marketing.accounts_error", source="ga4"
                        )
                    else:
                        parsed = ga4.parse_report("catalog", events.json())
                        ch = ga4.parse_report("channels", channels.json())
                        payload = {
                            "events": [[d[0], m[0]] for d, m in parsed.rows],
                            "channels": [d[0] for d, _ in ch.rows],
                            "dimensions": [
                                [d.get("parameterName", ""), d.get("displayName", "")]
                                for d in dims.json().get("customDimensions", [])
                                if d.get("scope", "EVENT") == "EVENT"
                            ],
                            "key_events": [
                                k.get("eventName", "") for k in keys.json().get("keyEvents", [])
                            ],
                        }
                        await redis.set(cache_key, json.dumps(payload), ex=CACHE_TTL)
            if payload is not None:
                out.ga4_available = True
                out.events = [CatalogEvent(name=n, count=c) for n, c in payload["events"]]
                out.channels = list(payload.get("channels") or [])
                out.custom_dimensions = [
                    CatalogDimension(parameter=p, field=f"customEvent:{p}", display_name=name)
                    for p, name in payload["dimensions"]
                    if p
                ]
                out.key_events = [k for k in payload["key_events"] if k]
        if gads_link is not None:
            cache_key = f"schakl:marketing:leads:catalog:ads:{gads_link.id}"
            cached = await redis.get(cache_key)
            payload = json.loads(cached) if cached is not None else None
            if payload is None:
                connection = await self._connection(gads_link)
                token = await resolve_ads_developer_token(
                    self.ctx.session, self.ctx.org.id, ctx=self.ctx
                )
                if connection is not None and token:
                    config = gads_link.config or {}
                    credentials = AdsCredentials(
                        developer_token=token,
                        login_customer_id=normalise_customer_id(config.get("manager_id")) or None,
                    )
                    try:
                        async with (
                            ads_client(
                                self.ctx.session,
                                self.ctx.org,
                                connection,
                                credentials,
                                tool="leads",
                            ) as client,
                            self.ctx.release_db(),
                        ):
                            rows = await client.search(
                                gads_link.external_id,
                                "SELECT conversion_action.name, conversion_action.category, "
                                "conversion_action.primary_for_goal, conversion_action.status "
                                "FROM conversion_action WHERE conversion_action.status = 'ENABLED'",
                                max_rows=500,
                                context="catalog",
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("leads catalog Ads read failed (%s): %s", gads_link.id, exc)
                    else:
                        payload = {
                            "actions": [
                                [
                                    r.get("conversionAction", {}).get("name", ""),
                                    r.get("conversionAction", {}).get("category"),
                                    r.get("conversionAction", {}).get("primaryForGoal"),
                                ]
                                for r in rows
                            ]
                        }
                        await redis.set(cache_key, json.dumps(payload), ex=CACHE_TTL)
            if payload is not None:
                out.ads_available = True
                out.conversion_actions = [
                    CatalogAction(name=n, category=c, primary=p)
                    for n, c, p in payload["actions"]
                    if n
                ]
        return out
