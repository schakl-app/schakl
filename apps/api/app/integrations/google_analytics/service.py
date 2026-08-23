"""What the Google Analytics surface answers, and how it refuses. Business-licensed — see LICENSE.

Every method here is a **read**. That is not a phase — the integration owns no table, mirrors
nothing, and there is nothing in a GA4 property this platform has any business writing: the
property belongs to the client, its configuration belongs to whoever set the tags up, and the
one thing an agency does with Analytics is ask it questions. So the whole surface is GET, which
also means it keeps answering past a licence expiry (§18: the write gate reads the method), and
that is the right way round — data is never hostage.

Four rules the shapes here follow, each learned somewhere else in this codebase first:

* **A credential's absence is evidence about that credential, never a verdict on the screen**
  (#399/#411). :meth:`properties` reports ``connected`` / ``has_scope`` instead of raising, so a
  picker can teach the state that fixes it. A call that names a property *does* refuse, because
  by then there is a specific thing the caller cannot have.
* **A short answer says it is short** (§17, and ``google_tag_manager`` §3a). A listing that hits
  its page ceiling sets ``truncated``; a report asked for 25 rows out of 400 says so. A prefix
  presented as a whole is the worst answer available, because it looks like it worked.
* **A refusal names a parameter; it does not pass a verdict on the endpoint** (``cloudflare``).
  Google's status and reason ride in ``details`` as identifiers, never as prose — its own English
  in the envelope is a screen in the wrong language (§9).
* **A ratio is weighted, so it is never summed.** Totals come from Google's own totals row.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

import httpx

from app.core.periods import ComparePeriod, compare_window, period_days, resolve_period
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.errors import AppError
from app.integrations.google import client as google_client
from app.integrations.google.models import ConnectionStatus, GoogleConnection
from app.integrations.google.oauth import SCOPE_ANALYTICS
from app.integrations.google_analytics.client import (
    ADMIN_API,
    DATA_API,
    get,
    list_all,
    post,
    property_path,
    report_rows,
    totals,
    transport,
)
from app.integrations.google_analytics.schemas import (
    GoogleAnalyticsChange,
    GoogleAnalyticsCompare,
    GoogleAnalyticsCompatibility,
    GoogleAnalyticsCompatibilityItem,
    GoogleAnalyticsField,
    GoogleAnalyticsMetadata,
    GoogleAnalyticsOverview,
    GoogleAnalyticsPeriod,
    GoogleAnalyticsProperty,
    GoogleAnalyticsPropertyList,
    GoogleAnalyticsRealtime,
    GoogleAnalyticsReport,
    GoogleAnalyticsResourceList,
    GoogleAnalyticsRow,
)

logger = logging.getLogger("schakl.google_analytics")

#: The Admin API listings this module offers, as ``url segment → (path suffix, response key)``.
#: A mapping rather than seven near-identical methods: they differ in two strings and nothing
#: else, and seven copies is six that will not gain the next fix.
RESOURCE_LISTINGS: dict[str, tuple[str, str]] = {
    "data-streams": ("dataStreams", "dataStreams"),
    "key-events": ("keyEvents", "keyEvents"),
    "custom-dimensions": ("customDimensions", "customDimensions"),
    "custom-metrics": ("customMetrics", "customMetrics"),
    "google-ads-links": ("googleAdsLinks", "googleAdsLinks"),
    "firebase-links": ("firebaseLinks", "firebaseLinks"),
}

#: The headline metrics an overview reports. ``keyEvents`` and never ``conversions``: the
#: retired name 400s the whole report rather than answering zero.
OVERVIEW_METRICS = (
    "sessions",
    "totalUsers",
    "newUsers",
    "screenPageViews",
    "keyEvents",
    "engagementRate",
    "averageSessionDuration",
    "bounceRate",
    "totalRevenue",
)

#: What a caller gets when they ask for a series and name no metrics.
DEFAULT_SERIES_METRICS = ("sessions", "totalUsers", "keyEvents")

#: Ceilings. GA4 will return 100 000 rows and a model will read none of them.
MAX_ROWS = 250
DEFAULT_ROWS = 25
MAX_REALTIME_ROWS = 50


class GoogleAnalyticsService:
    """Read-only GA4 access for one request, under that request's own context."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # --- the credential --------------------------------------------------------------------- #
    async def _connection(self) -> GoogleConnection | None:
        return await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )

    @staticmethod
    def _has_scope(connection: GoogleConnection) -> bool:
        return SCOPE_ANALYTICS in set(connection.scopes or [])

    async def _connection_or_refuse(self) -> GoogleConnection:
        """The caller's own Google connection, or the refusal that names what is missing.

        Three different states and three different sentences, because exactly one person can act
        on each: nobody has connected Google (connect), the grant does not carry Analytics
        (re-consent — a *different* button), or the grant has died (reconnect).
        """
        connection = await self._connection()
        if connection is None:
            raise AppError(
                "google_not_connected", "errors.google_not_connected", status_code=409
            )
        if connection.status != ConnectionStatus.ACTIVE.value:
            raise AppError(
                "google_connection_error", "errors.google_connection_error", status_code=409
            )
        if not self._has_scope(connection):
            raise AppError(
                "google_analytics_scope_missing",
                "errors.google_analytics_scope_missing",
                status_code=409,
                details={"scope": SCOPE_ANALYTICS},
            )
        return connection

    @asynccontextmanager
    async def _client(self, connection: GoogleConnection):
        """An authenticated client with the database connection handed back for the round trip.

        ``acting_as`` is entered first because it reads settings, then the pool connection is
        released: a GA4 report is a second or two of somebody else's latency, and holding a
        Postgres connection across it is how thirty clients drain a pool at four in the morning
        (docs/PERFORMANCE.md, CLAUDE.md §11).
        """
        async with (
            google_client.acting_as(
                self.ctx.session, self.ctx.org, connection, transport=transport()
            ) as client,
            self.ctx.release_db(),
        ):
            try:
                yield client
            except httpx.HTTPStatusError as exc:
                raise self._translate(exc) from exc

    def _translate(self, exc: httpx.HTTPStatusError) -> AppError:
        """Google's refusal as this platform's envelope.

        ``details`` carries Google's own identifiers — a status enum and a reason code — because
        those are what a caller can branch on, and never Google's message, which is untranslated
        English written for somebody else's console (§9,
        ``test_google_s_own_text_never_reaches_the_envelope``).
        """
        detail = google_client.describe_api_error(exc)
        logger.warning("google analytics call failed: %s", detail or exc)
        if detail is not None and detail.scope_insufficient:
            return AppError(
                "google_analytics_scope_missing",
                "errors.google_analytics_scope_missing",
                status_code=409,
                details={"scope": SCOPE_ANALYTICS},
            )
        status = exc.response.status_code if exc.response is not None else 502
        details: dict[str, Any] = {"google_http_status": status}
        if detail is not None:
            if detail.status:
                details["google_status"] = detail.status
            if detail.reason:
                details["google_reason"] = detail.reason
        if status in (401, 403):
            return AppError(
                "google_analytics_denied",
                "errors.google_analytics_denied",
                status_code=403,
                details=details,
            )
        if status == 400:
            # A malformed report is the caller's, and it is the commonest failure here: a metric
            # that does not exist, or a pair GA4 refuses to combine. 422 rather than 502, so an
            # agent reads "fix your request" and not "the provider is down".
            return AppError(
                "google_analytics_invalid_request",
                "errors.google_analytics_invalid_request",
                status_code=422,
                details=details,
            )
        return AppError(
            "google_analytics_unavailable",
            "errors.google_analytics_unavailable",
            status_code=502,
            details=details,
        )

    # --- periods ---------------------------------------------------------------------------- #
    async def _window(self, period: str | None) -> tuple[Any, Any]:
        """The span a token names, in the **org's** calendar (CLAUDE.md §8).

        A GA4 property keeps its own reporting timezone, which is reported beside the numbers
        rather than used here: substituting the property's clock for the org's would make two
        clients' dashboards disagree about what "last month" is, and the platform has one answer
        to that question already.
        """
        today = await org_today(self.ctx.session, self.ctx.org.id)
        return resolve_period(period, today)

    @staticmethod
    def _period_out(start: Any, end: Any) -> GoogleAnalyticsPeriod:
        return GoogleAnalyticsPeriod(
            date_from=start, date_to=end, days=period_days(start, end)
        )

    # --- the property list ------------------------------------------------------------------ #
    async def properties(self, query: str | None = None) -> GoogleAnalyticsPropertyList:
        """Every GA4 property this connection reaches, or the state that explains why none."""
        connection = await self._connection()
        if connection is None:
            return GoogleAnalyticsPropertyList(connected=False, has_scope=False)
        has_scope = self._has_scope(connection)
        if connection.status != ConnectionStatus.ACTIVE.value or not has_scope:
            return GoogleAnalyticsPropertyList(connected=True, has_scope=has_scope)

        async with self._client(connection) as client:
            summaries, truncated = await list_all(
                client, ADMIN_API, "accountSummaries", "accountSummaries"
            )

        needle = (query or "").strip().casefold()
        rows: list[GoogleAnalyticsProperty] = []
        for account in summaries:
            account_name = account.get("displayName") or ""
            account_id = str(account.get("account") or "")
            for item in account.get("propertySummaries") or []:
                resource = str(item.get("property") or "")
                if not resource:
                    continue
                display = item.get("displayName") or resource
                if needle and needle not in display.casefold() and needle not in resource:
                    continue
                rows.append(
                    GoogleAnalyticsProperty(
                        property_id=resource.split("/")[-1],
                        display_name=display,
                        account_id=account_id.split("/")[-1],
                        account_name=account_name,
                        property_type=item.get("propertyType") or "",
                        parent=str(item.get("parent") or ""),
                    )
                )
        rows.sort(key=lambda row: row.display_name.casefold())
        return GoogleAnalyticsPropertyList(
            connected=True, has_scope=True, properties=rows, truncated=truncated
        )

    async def property_detail(self, property_id: str) -> GoogleAnalyticsProperty:
        """One property's own record — its currency and its **reporting timezone**.

        Worth a call of its own precisely for that second field: every date in every report below
        is a day in the property's zone, and a report compared against anything this platform
        computed is only sound once somebody has seen that the two agree.
        """
        connection = await self._connection_or_refuse()
        path = property_path(property_id)
        async with self._client(connection) as client:
            body = await get(client, ADMIN_API, path)
        return GoogleAnalyticsProperty(
            property_id=str(body.get("name") or path).split("/")[-1],
            display_name=body.get("displayName") or "",
            property_type=body.get("propertyType") or "",
            currency_code=body.get("currencyCode") or "",
            time_zone=body.get("timeZone") or "",
            industry_category=body.get("industryCategory") or "",
            parent=str(body.get("parent") or ""),
            account_id=str(body.get("parent") or "").split("/")[-1],
        )

    async def resources(self, property_id: str, kind: str) -> GoogleAnalyticsResourceList:
        """One Admin API listing for a property, verbatim (see :data:`RESOURCE_LISTINGS`)."""
        if kind not in RESOURCE_LISTINGS:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                details={"kind": sorted(RESOURCE_LISTINGS)},
            )
        connection = await self._connection_or_refuse()
        suffix, key = RESOURCE_LISTINGS[kind]
        path = f"{property_path(property_id)}/{suffix}"
        async with self._client(connection) as client:
            rows, truncated = await list_all(client, ADMIN_API, path, key)
        return GoogleAnalyticsResourceList(
            property_id=property_path(property_id).split("/")[-1],
            kind=kind,
            rows=rows,
            truncated=truncated,
        )

    async def data_retention(self, property_id: str) -> GoogleAnalyticsResourceList:
        """The property's data retention settings — a singleton, not a listing.

        Its own method because it is the one Admin resource that is not a list, and folding it
        into :meth:`resources` would mean a ``rows`` of exactly one for no reason. It answers a
        real question: a property set to two months cannot report on last year, and "there is no
        data" and "the data was deleted by policy" look identical in a chart.
        """
        connection = await self._connection_or_refuse()
        path = f"{property_path(property_id)}/dataRetentionSettings"
        async with self._client(connection) as client:
            body = await get(client, ADMIN_API, path)
        return GoogleAnalyticsResourceList(
            property_id=property_path(property_id).split("/")[-1],
            kind="data-retention",
            rows=[body] if body else [],
        )

    # --- what this property will answer ------------------------------------------------------ #
    async def metadata(self, property_id: str) -> GoogleAnalyticsMetadata:
        """Every dimension and metric this property accepts, custom ones included.

        The custom half is why this is not a static list: an agency's own event parameters
        surface here under names nobody could guess, and they are the fields a client's report
        is actually about.
        """
        connection = await self._connection_or_refuse()
        path = f"{property_path(property_id)}/metadata"
        async with self._client(connection) as client:
            body = await get(client, DATA_API, path)
        return GoogleAnalyticsMetadata(
            property_id=property_path(property_id).split("/")[-1],
            dimensions=[
                GoogleAnalyticsField(
                    api_name=item.get("apiName", ""),
                    ui_name=item.get("uiName", ""),
                    description=item.get("description", ""),
                    category=item.get("category", ""),
                    custom=bool(item.get("customDefinition")),
                )
                for item in body.get("dimensions") or []
            ],
            metrics=[
                GoogleAnalyticsField(
                    api_name=item.get("apiName", ""),
                    ui_name=item.get("uiName", ""),
                    description=item.get("description", ""),
                    category=item.get("category", ""),
                    type=item.get("type", ""),
                    custom=bool(item.get("customDefinition")),
                )
                for item in body.get("metrics") or []
            ],
        )

    async def compatibility(
        self, property_id: str, dimensions: list[str], metrics: list[str]
    ) -> GoogleAnalyticsCompatibility:
        """Which fields may still be added to the ones named. Ask before retrying a 400."""
        connection = await self._connection_or_refuse()
        path = f"{property_path(property_id)}:checkCompatibility"
        body: dict[str, Any] = {"compatibilityFilter": "COMPATIBLE"}
        if dimensions:
            body["dimensions"] = [{"name": name} for name in dimensions]
        if metrics:
            body["metrics"] = [{"name": name} for name in metrics]
        async with self._client(connection) as client:
            answer = await post(client, DATA_API, path, body)
        return GoogleAnalyticsCompatibility(
            property_id=property_path(property_id).split("/")[-1],
            dimensions=[
                GoogleAnalyticsCompatibilityItem(
                    api_name=(item.get("dimensionMetadata") or {}).get("apiName", ""),
                    compatibility=item.get("compatibility", ""),
                )
                for item in answer.get("dimensionCompatibilities") or []
            ],
            metrics=[
                GoogleAnalyticsCompatibilityItem(
                    api_name=(item.get("metricMetadata") or {}).get("apiName", ""),
                    compatibility=item.get("compatibility", ""),
                )
                for item in answer.get("metricCompatibilities") or []
            ],
        )

    # --- reports ----------------------------------------------------------------------------- #
    @staticmethod
    def _warnings(report: dict) -> list[str]:
        """What GA4 says about the *quality* of the answer, as keys rather than prose.

        Both states matter and neither is visible in the numbers: a sampled report is an estimate
        somebody will read as a count, and a thresholded one silently withholds rows about small
        audiences — so a total that does not add up is a fact about Google's privacy rules, not a
        bug in whoever is reading it.
        """
        metadata = report.get("metadata") or {}
        found: list[str] = []
        if metadata.get("samplingMetadatas"):
            found.append("google_analytics.warning.sampled")
        if metadata.get("subjectToThresholding"):
            found.append("google_analytics.warning.thresholded")
        if metadata.get("dataLossFromOtherRow"):
            found.append("google_analytics.warning.other_row")
        return found

    @staticmethod
    def _request(
        *,
        start: Any,
        end: Any,
        dimensions: list[str],
        metrics: list[str],
        limit: int,
        offset: int = 0,
        order: str | None = None,
        dimension_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [{"name": name} for name in dimensions],
            "metrics": [{"name": name} for name in metrics],
            "limit": limit,
            "offset": offset,
            # Off by default: a table of zeroes is not an answer, it is every combination that
            # never happened.
            "keepEmptyRows": False,
        }
        if order:
            desc = order.startswith("-")
            field = order.lstrip("-")
            if field in metrics:
                body["orderBys"] = [{"metric": {"metricName": field}, "desc": desc}]
            elif field in dimensions:
                body["orderBys"] = [{"dimension": {"dimensionName": field}, "desc": desc}]
        if dimension_filter:
            body["dimensionFilter"] = dimension_filter
        return body

    def _report_out(
        self, property_id: str, report: dict, start: Any, end: Any, limit: int
    ) -> GoogleAnalyticsReport:
        dimension_names, metric_names, rows = report_rows(report)
        row_count = int(report.get("rowCount") or len(rows))
        return GoogleAnalyticsReport(
            property_id=property_id,
            period=self._period_out(start, end),
            dimensions=dimension_names,
            metrics=metric_names,
            rows=[GoogleAnalyticsRow(**row) for row in rows],
            totals=totals(report, metric_names),
            row_count=row_count,
            truncated=row_count > len(rows),
            warnings=self._warnings(report),
        )

    async def report(
        self,
        property_id: str,
        *,
        dimensions: list[str],
        metrics: list[str],
        period: str | None = None,
        limit: int = DEFAULT_ROWS,
        offset: int = 0,
        order: str | None = None,
        filters: list[str] | None = None,
    ) -> GoogleAnalyticsReport:
        """The escape hatch: any dimensions crossed with any metrics over any period.

        Bounded in the two ways that matter and in no others. The **property is in the path**,
        taken from a listing this connection can already reach, so no report can be aimed at a
        property the caller could not have named. And the row count is clamped, because the
        difference between 25 rows and 100 000 is not a difference in the question.
        """
        if not metrics:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"metrics": "errors.required"},
            )
        connection = await self._connection_or_refuse()
        start, end = await self._window(period)
        limit = max(1, min(MAX_ROWS, limit))
        path = f"{property_path(property_id)}:runReport"
        body = self._request(
            start=start,
            end=end,
            dimensions=dimensions,
            metrics=metrics,
            limit=limit,
            offset=max(0, offset),
            order=order,
            dimension_filter=parse_filters(filters or []),
        )
        async with self._client(connection) as client:
            answer = await post(client, DATA_API, path, body)
        return self._report_out(
            property_path(property_id).split("/")[-1], answer, start, end, limit
        )

    async def pivot(
        self,
        property_id: str,
        *,
        dimensions: list[str],
        metrics: list[str],
        pivot_on: str,
        period: str | None = None,
        limit: int = DEFAULT_ROWS,
    ) -> GoogleAnalyticsReport:
        """One dimension crossed against another — channel by month, device by landing page.

        A separate endpoint rather than a flag, because GA4's pivot report is a different request
        shape with a different response shape, and a parameter that silently changes both is how
        a caller ends up reading one as the other.
        """
        if not metrics or not pivot_on:
            raise AppError("validation", "errors.validation", status_code=422)
        connection = await self._connection_or_refuse()
        start, end = await self._window(period)
        limit = max(1, min(MAX_ROWS, limit))
        rows_dimension = dimensions or [pivot_on]
        body = {
            "dateRanges": [{"startDate": start.isoformat(), "endDate": end.isoformat()}],
            "dimensions": [
                {"name": name} for name in dict.fromkeys([*rows_dimension, pivot_on])
            ],
            "metrics": [{"name": name} for name in metrics],
            "pivots": [
                {"fieldNames": rows_dimension, "limit": limit},
                {"fieldNames": [pivot_on], "limit": limit},
            ],
        }
        path = f"{property_path(property_id)}:runPivotReport"
        async with self._client(connection) as client:
            answer = await post(client, DATA_API, path, body)
        return self._report_out(
            property_path(property_id).split("/")[-1], answer, start, end, limit
        )

    async def breakdown(
        self,
        property_id: str,
        *,
        dimension: str,
        metrics: list[str] | None = None,
        period: str | None = None,
        limit: int = DEFAULT_ROWS,
        order: str | None = None,
        filters: list[str] | None = None,
    ) -> GoogleAnalyticsReport:
        """One dimension, ranked. The shape every "top pages / channels / sources" question is."""
        chosen = metrics or list(DEFAULT_SERIES_METRICS)
        return await self.report(
            property_id,
            dimensions=[dimension],
            metrics=chosen,
            period=period,
            limit=limit,
            order=order or f"-{chosen[0]}",
            filters=filters,
        )

    async def timeseries(
        self,
        property_id: str,
        *,
        metrics: list[str] | None = None,
        period: str | None = None,
    ) -> GoogleAnalyticsReport:
        """Day by day, oldest first — the series a chart is drawn from."""
        chosen = metrics or list(DEFAULT_SERIES_METRICS)
        return await self.report(
            property_id,
            dimensions=["date"],
            metrics=chosen,
            period=period,
            limit=MAX_ROWS,
            order="date",
        )

    async def overview(
        self,
        property_id: str,
        *,
        period: str | None = None,
        compare: str | None = None,
    ) -> GoogleAnalyticsOverview:
        """This period, the one it is measured against, and the change — in one round trip.

        Three questions (now, then, and where the traffic came from) are one ``batchRunReports``
        rather than three calls, because GA4's quota is per property per day and a screen that
        costs three of everything is a screen somebody stops opening (docs/PERFORMANCE.md).

        The comparison defaults to **the same period a year earlier**, which is the platform's
        own default (#312) and the one seasonality survives. It is stated in the answer either
        way: "up 20 %" over an unnamed span is not a claim anybody can check.
        """
        connection = await self._connection_or_refuse()
        start, end = await self._window(period)
        mode = (
            ComparePeriod.PREVIOUS
            if (compare or "").lower() == ComparePeriod.PREVIOUS.value
            else ComparePeriod.YEAR
        )
        before_start, before_end = compare_window(start, end, mode)
        metrics = list(OVERVIEW_METRICS)
        path = f"{property_path(property_id)}:batchRunReports"
        body = {
            "requests": [
                self._request(
                    start=start, end=end, dimensions=[], metrics=metrics, limit=1
                ),
                self._request(
                    start=before_start,
                    end=before_end,
                    dimensions=[],
                    metrics=metrics,
                    limit=1,
                ),
                self._request(
                    start=start,
                    end=end,
                    dimensions=["sessionDefaultChannelGroup"],
                    metrics=["sessions"],
                    limit=25,
                    order="-sessions",
                ),
            ]
        }
        async with self._client(connection) as client:
            answer = await post(client, DATA_API, path, body)
        reports = answer.get("reports") or []
        current = reports[0] if len(reports) > 0 else {}
        previous = reports[1] if len(reports) > 1 else {}
        channels_report = reports[2] if len(reports) > 2 else {}

        now = totals(current, metrics)
        then = totals(previous, metrics)
        _, _, channel_rows = report_rows(channels_report)
        channels = {
            (row["dimensions"].get("sessionDefaultChannelGroup") or "Other"): row["metrics"].get(
                "sessions", 0.0
            )
            for row in channel_rows
        }
        metadata = current.get("metadata") or {}
        return GoogleAnalyticsOverview(
            property_id=property_path(property_id).split("/")[-1],
            period=self._period_out(start, end),
            compared_with=GoogleAnalyticsCompare(
                date_from=before_start, date_to=before_end, mode=mode.value
            ),
            currency_code=metadata.get("currencyCode") or "",
            time_zone=metadata.get("timeZone") or "",
            totals=now,
            previous_totals=then,
            change={key: _change(now.get(key), then.get(key)) for key in metrics},
            channels=channels,
            warnings=self._warnings(current),
        )

    async def realtime(
        self,
        property_id: str,
        *,
        dimensions: list[str] | None = None,
        metrics: list[str] | None = None,
        limit: int = MAX_REALTIME_ROWS,
    ) -> GoogleAnalyticsRealtime:
        """Who is on the site in the last half hour. No period parameter exists, by design."""
        connection = await self._connection_or_refuse()
        chosen_metrics = metrics or ["activeUsers"]
        chosen_dimensions = dimensions or []
        body: dict[str, Any] = {
            "dimensions": [{"name": name} for name in chosen_dimensions],
            "metrics": [{"name": name} for name in chosen_metrics],
            "limit": max(1, min(MAX_REALTIME_ROWS, limit)),
        }
        path = f"{property_path(property_id)}:runRealtimeReport"
        async with self._client(connection) as client:
            answer = await post(client, DATA_API, path, body)
        dimension_names, metric_names, rows = report_rows(answer)
        headline = totals(answer, metric_names)
        return GoogleAnalyticsRealtime(
            property_id=property_path(property_id).split("/")[-1],
            active_users=headline.get(
                "activeUsers", sum(row["metrics"].get("activeUsers", 0.0) for row in rows)
            ),
            dimensions=dimension_names,
            metrics=metric_names,
            rows=[GoogleAnalyticsRow(**row) for row in rows],
        )


def _change(now: Any, then: Any) -> GoogleAnalyticsChange | None:
    if not isinstance(now, int | float) or not isinstance(then, int | float):
        return None
    absolute = round(now - then, 4)
    return GoogleAnalyticsChange(
        value_from=then,
        value_to=now,
        absolute=absolute,
        relative=round(absolute / then, 4) if then else None,
    )


#: The filter grammar the query string accepts. Deliberately three operators and no parser:
#: a query language in a query parameter is a second API nobody documented, and everything
#: richer than this belongs in a report body — which this surface does not take, on purpose.
_OPERATORS = (("==", "EXACT"), ("=@", "CONTAINS"), ("=^", "BEGINS_WITH"))


def parse_filters(raw: list[str]) -> dict[str, Any] | None:
    """``sessionSource==google`` / ``pagePath=@/blog`` → a GA4 ``dimensionFilter``.

    A malformed clause is **refused**, never dropped: a filter silently ignored answers a
    different question than the one asked with every row valid and the total wrong — the
    SnelStart ``$filter`` lesson, which is the same failure a query string can produce here.
    """
    clauses: list[dict[str, Any]] = []
    for item in raw:
        for token, match_type in _OPERATORS:
            field, sep, value = item.partition(token)
            if sep and field.strip() and value.strip():
                clauses.append(
                    {
                        "filter": {
                            "fieldName": field.strip(),
                            "stringFilter": {
                                "matchType": match_type,
                                "value": value.strip(),
                                "caseSensitive": False,
                            },
                        }
                    }
                )
                break
        else:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"filter": "errors.validation"},
                details={"filter": item, "operators": [token for token, _ in _OPERATORS]},
            )
    if not clauses:
        return None
    if len(clauses) == 1:
        return clauses[0]
    return {"andGroup": {"expressions": clauses}}
