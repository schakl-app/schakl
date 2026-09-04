"""What the Search Console surface answers, and how it refuses. Business-licensed — see LICENSE.

Every method here is a **read**. The integration owns no table, mirrors nothing, and there is
nothing in a client's Search Console property this platform has any business writing: a sitemap
is submitted by whoever deploys the site, a property is verified by whoever owns the domain, and
the one thing an agency does with Search Console is ask it questions. So the whole surface is
GET, which also means it keeps answering past a licence expiry (§18: the write gate reads the
method), and that is the right way round — data is never hostage.

Four rules the shapes follow, each learned somewhere else in this codebase first:

* **A credential's absence is evidence about that credential, never a verdict on the screen**
  (#399/#411). :meth:`sites` reports ``connected`` / ``has_scope`` instead of raising, so a
  picker can teach the state that fixes it. A call that names a site *does* refuse, because by
  then there is a specific thing the caller cannot have.
* **A short answer says it is short** (§17). Google reports no row total, so every paged read
  asks for one row more than it keeps and sets ``truncated`` when it arrives — the same device
  the task comments use, applied to a provider that cannot count for us.
* **A refusal names a parameter; it does not pass a verdict on the endpoint** (``cloudflare``).
  Google's status and reason ride in ``details`` as identifiers, never as prose — its own English
  in the envelope is a screen in the wrong language (§9).
* **A fresh number says it is fresh.** Search Console finalises two to three days late, and the
  curated reads ask for ``dataState: all`` so yesterday exists at all; the first day Google is
  still collecting comes back as ``fresh_from``, because a number that will move tomorrow should
  not be read as one that will not.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from app.core.periods import ComparePeriod, compare_window, period_days, resolve_period
from app.core.tenancy import RequestContext
from app.core.timezone import org_today
from app.errors import AppError
from app.integrations.google import client as google_client
from app.integrations.google.models import ConnectionStatus, GoogleConnection
from app.integrations.google.oauth import SCOPE_SEARCH_CONSOLE
from app.integrations.google_search_console.client import (
    AGGREGATIONS,
    API_REVISION_CHECKED,
    DATA_STATES,
    DEFAULT_ROWS,
    DIMENSIONS,
    FILTER_DIMENSIONS,
    GENERATIVE_AI_SEARCH_TYPES,
    HOURLY_DAYS,
    INSPECTION_API,
    LOWER_IS_BETTER,
    MAX_ROWS,
    METRICS,
    ORDER_WINDOW,
    SEARCH_TYPES,
    WEBMASTERS_API,
    console_url,
    display_name,
    generative_ai_report_url,
    get,
    post,
    rows_out,
    site_key,
    site_type,
    site_url,
    transport,
)
from app.integrations.google_search_console.schemas import (
    GoogleSearchConsoleAiSource,
    GoogleSearchConsoleAiVisibility,
    GoogleSearchConsoleChange,
    GoogleSearchConsoleCompare,
    GoogleSearchConsoleInspection,
    GoogleSearchConsoleMover,
    GoogleSearchConsoleMovers,
    GoogleSearchConsoleOverview,
    GoogleSearchConsolePeriod,
    GoogleSearchConsoleReport,
    GoogleSearchConsoleRow,
    GoogleSearchConsoleSearchTypes,
    GoogleSearchConsoleSite,
    GoogleSearchConsoleSiteList,
    GoogleSearchConsoleSitemap,
    GoogleSearchConsoleSitemapList,
)

logger = logging.getLogger("schakl.google_search_console")

#: The i18n key the AI-visibility answer carries while the API has no search type for it.
AI_NOT_IN_API = "google_search_console.ai_visibility.not_in_api"

#: What a listing of movers ignores: a term shown twice all month is a rounding error, and a
#: table of them buries the ones the client is actually competing for.
DEFAULT_MIN_IMPRESSIONS = 10.0


class GoogleSearchConsoleService:
    """Read-only Search Console access for one request, under that request's own context."""

    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx

    # --- the credential --------------------------------------------------------------------- #
    async def _connection(self) -> GoogleConnection | None:
        return await google_client.connection_for(
            self.ctx.session, self.ctx.org.id, self.ctx.user.id
        )

    @staticmethod
    def _has_scope(connection: GoogleConnection) -> bool:
        return SCOPE_SEARCH_CONSOLE in set(connection.scopes or [])

    async def _connection_or_refuse(self) -> GoogleConnection:
        """The caller's own Google connection, or the refusal that names what is missing.

        Three different states and three different sentences, because exactly one person can act
        on each: nobody has connected Google (connect), the grant does not carry Search Console
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
                "google_search_console_scope_missing",
                "errors.google_search_console_scope_missing",
                status_code=409,
                details={"scope": SCOPE_SEARCH_CONSOLE},
            )
        return connection

    @asynccontextmanager
    async def _client(self, connection: GoogleConnection):
        """An authenticated client with the database connection handed back for the round trip.

        ``acting_as`` is entered first because it reads settings, then the pool connection is
        released: a Search Analytics query is a second of somebody else's latency, and holding a
        Postgres connection across it is how thirty clients drain a pool (docs/PERFORMANCE.md).
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
        English written for somebody else's console (§9).
        """
        detail = google_client.describe_api_error(exc)
        logger.warning("google search console call failed: %s", detail or exc)
        if detail is not None and detail.scope_insufficient:
            return AppError(
                "google_search_console_scope_missing",
                "errors.google_search_console_scope_missing",
                status_code=409,
                details={"scope": SCOPE_SEARCH_CONSOLE},
            )
        status = exc.response.status_code if exc.response is not None else 502
        details: dict[str, Any] = {"google_http_status": status}
        if detail is not None:
            if detail.status:
                details["google_status"] = detail.status
            if detail.reason:
                details["google_reason"] = detail.reason
        if status in (401, 403):
            # Also what Google answers for a property this account is not a user of: "not
            # yours" and "not allowed" are one status code on its side.
            return AppError(
                "google_search_console_denied",
                "errors.google_search_console_denied",
                status_code=403,
                details=details,
            )
        if status == 404:
            return AppError(
                "google_search_console_site_not_found",
                "errors.google_search_console_site_not_found",
                status_code=404,
                details=details,
            )
        if status == 400:
            # A malformed query is the caller's: a dimension the API does not know, a filter on
            # a field it cannot filter, a date range in the future. 422 rather than 502, so an
            # agent reads "fix your request" and not "the provider is down".
            return AppError(
                "google_search_console_invalid_request",
                "errors.google_search_console_invalid_request",
                status_code=422,
                details=details,
            )
        if status == 429:
            # Per-site, per-user and per-project quotas all answer 429. A rate is not a verdict
            # (the Cloudflare probe rule): it is a wait, and the status says so.
            return AppError(
                "google_search_console_quota",
                "errors.google_search_console_quota",
                status_code=429,
                details=details,
            )
        return AppError(
            "google_search_console_unavailable",
            "errors.google_search_console_unavailable",
            status_code=502,
            details=details,
        )

    # --- periods ---------------------------------------------------------------------------- #
    async def _today(self) -> date:
        return await org_today(self.ctx.session, self.ctx.org.id)

    async def _window(self, period: str | None) -> tuple[date, date]:
        """The span a token names, in the **org's** calendar (CLAUDE.md §8).

        Search Console keeps its days in Pacific time, which is neither the org's nor the
        viewer's. The dates are passed as the org resolved them and the fact is documented
        beside the numbers rather than corrected here: the platform has one answer to "what is
        last month" and this must not become a second one.
        """
        return resolve_period(period, await self._today())

    @staticmethod
    def _period_out(start: date, end: date) -> GoogleSearchConsolePeriod:
        return GoogleSearchConsolePeriod(
            date_from=start, date_to=end, days=period_days(start, end)
        )

    @staticmethod
    def _compare_mode(compare: str | None) -> ComparePeriod:
        return (
            ComparePeriod.PREVIOUS
            if (compare or "").lower() == ComparePeriod.PREVIOUS.value
            else ComparePeriod.YEAR
        )

    # --- validation -------------------------------------------------------------------------- #
    @staticmethod
    def _one_of(value: str | None, allowed: tuple[str, ...], field: str, default: str) -> str:
        """A vocabulary value, or a 422 that names the field *and* what would have worked.

        Google's own refusal for an unknown dimension is a 400 whose message names neither, and
        this module knows the list — so it says it, before the round trip is spent (§9's
        ``details``: the machine-readable half, so an agent can correct without a second call).
        """
        chosen = (value or "").strip() or default
        by_fold = {item.casefold(): item for item in allowed}
        if chosen.casefold() not in by_fold:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={field: "errors.validation"},
                details={field: chosen, "allowed": list(allowed)},
            )
        return by_fold[chosen.casefold()]

    @staticmethod
    def _fresh_from(metadata: dict) -> date | None:
        raw = (metadata or {}).get("firstIncompleteDate")
        try:
            return date.fromisoformat(str(raw)) if raw else None
        except ValueError:
            return None

    # --- the query --------------------------------------------------------------------------- #
    async def _query(
        self,
        client: Any,
        site: str,
        *,
        start: date,
        end: date,
        dimensions: list[str],
        search_type: str = "web",
        filters: list[dict[str, str]] | None = None,
        aggregation: str | None = None,
        data_state: str = "all",
        limit: int,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], dict]:
        """One Search Analytics query, as ``(rows, metadata)``.

        ``dataState: all`` by default rather than Google's ``final``: with the default, the last
        two or three days simply do not exist, and "how did we do yesterday" answers nothing.
        The price is that the fresh days may still move, which :func:`_fresh_from` reports.
        """
        body: dict[str, Any] = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "dimensions": list(dimensions),
            "type": search_type,
            "rowLimit": max(1, min(25_000, limit)),
            "startRow": max(0, offset),
            "dataState": data_state,
        }
        if filters:
            body["dimensionFilterGroups"] = [{"groupType": "and", "filters": filters}]
        if aggregation and aggregation != "auto":
            body["aggregationType"] = aggregation
        answer = await post(
            client, f"{WEBMASTERS_API}/sites/{site_key(site)}/searchAnalytics/query", body
        )
        return rows_out(answer.get("rows") or [], list(dimensions)), answer.get("metadata") or {}

    @staticmethod
    def _totals(rows: list[dict[str, Any]]) -> dict[str, float]:
        """The one row a dimension-less query answers, or zeroes when Google answered none.

        Zeroes rather than an absence: a period with no impressions is a real answer, and the
        change against it must be computable (relative ``None``, absolute the whole figure).
        """
        if rows:
            return dict(rows[0]["metrics"])
        return {metric: 0.0 for metric in METRICS}

    # --- what exists ------------------------------------------------------------------------- #
    async def sites(self, query: str | None = None) -> GoogleSearchConsoleSiteList:
        """Every property this connection reaches, or the state that explains why none."""
        connection = await self._connection()
        if connection is None:
            return GoogleSearchConsoleSiteList(connected=False, has_scope=False)
        has_scope = self._has_scope(connection)
        if connection.status != ConnectionStatus.ACTIVE.value or not has_scope:
            return GoogleSearchConsoleSiteList(connected=True, has_scope=has_scope)

        async with self._client(connection) as client:
            body = await get(client, f"{WEBMASTERS_API}/sites")

        needle = (query or "").strip().casefold()
        rows: list[GoogleSearchConsoleSite] = []
        for entry in body.get("siteEntry") or []:
            url = str(entry.get("siteUrl") or "")
            if not url:
                continue
            if needle and needle not in url.casefold():
                continue
            rows.append(self._site_out(url, entry))
        rows.sort(key=lambda row: row.display_name.casefold())
        return GoogleSearchConsoleSiteList(connected=True, has_scope=True, sites=rows)

    @staticmethod
    def _site_out(url: str, entry: dict) -> GoogleSearchConsoleSite:
        return GoogleSearchConsoleSite(
            site_url=url,
            display_name=display_name(url),
            site_type=site_type(url),
            permission_level=str(entry.get("permissionLevel") or ""),
            console_url=console_url(url),
        )

    async def site_detail(self, site: str) -> GoogleSearchConsoleSite:
        """One property's record — chiefly what this account may do there."""
        connection = await self._connection_or_refuse()
        url = site_url(site)
        async with self._client(connection) as client:
            body = await get(client, f"{WEBMASTERS_API}/sites/{site_key(url)}")
        return self._site_out(str(body.get("siteUrl") or url), body)

    async def sitemaps(self, site: str) -> GoogleSearchConsoleSitemapList:
        """Every sitemap submitted for the property, with what Google made of each."""
        connection = await self._connection_or_refuse()
        url = site_url(site)
        async with self._client(connection) as client:
            body = await get(client, f"{WEBMASTERS_API}/sites/{site_key(url)}/sitemaps")
        return GoogleSearchConsoleSitemapList(
            site_url=url,
            sitemaps=[self._sitemap_out(item) for item in body.get("sitemap") or []],
        )

    async def sitemap(self, site: str, feedpath: str) -> GoogleSearchConsoleSitemap:
        """One sitemap by its own URL."""
        connection = await self._connection_or_refuse()
        url = site_url(site)
        path = str(feedpath or "").strip()
        if not path:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"feedpath": "errors.required"},
            )
        async with self._client(connection) as client:
            body = await get(
                client,
                f"{WEBMASTERS_API}/sites/{site_key(url)}/sitemaps/{quote(path, safe='')}",
            )
        return self._sitemap_out(body)

    @staticmethod
    def _sitemap_out(item: dict) -> GoogleSearchConsoleSitemap:
        def _int(raw: Any) -> int:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return 0

        return GoogleSearchConsoleSitemap(
            path=str(item.get("path") or ""),
            type=str(item.get("type") or ""),
            last_submitted=item.get("lastSubmitted"),
            last_downloaded=item.get("lastDownloaded"),
            is_pending=bool(item.get("isPending")),
            is_sitemaps_index=bool(item.get("isSitemapsIndex")),
            # Google sends these two as strings, the way it sends every number in this API.
            errors=_int(item.get("errors")),
            warnings=_int(item.get("warnings")),
            contents=[
                {"type": str(c.get("type") or ""), "submitted": _int(c.get("submitted"))}
                for c in item.get("contents") or []
            ],
        )

    # --- what happened ----------------------------------------------------------------------- #
    async def overview(
        self,
        site: str,
        *,
        period: str | None = None,
        compare: str | None = None,
        search_type: str | None = None,
    ) -> GoogleSearchConsoleOverview:
        """This period, the one it is measured against, the change, and the device split.

        Three queries in flight together rather than in sequence: Search Console's quota is
        generous (1 200 a minute per site) and the cost that matters on a screen is the wall
        clock. The comparison defaults to **the same period a year earlier**, the platform's own
        default (#312), and is stated in the answer either way.
        """
        connection = await self._connection_or_refuse()
        url = site_url(site)
        kind = self._one_of(search_type, SEARCH_TYPES, "search_type", "web")
        start, end = await self._window(period)
        mode = self._compare_mode(compare)
        before_start, before_end = compare_window(start, end, mode)
        async with self._client(connection) as client:
            (now_rows, metadata), (then_rows, _), (device_rows, _) = await asyncio.gather(
                self._query(
                    client, url, start=start, end=end, dimensions=[], search_type=kind, limit=1
                ),
                self._query(
                    client,
                    url,
                    start=before_start,
                    end=before_end,
                    dimensions=[],
                    search_type=kind,
                    limit=1,
                ),
                self._query(
                    client,
                    url,
                    start=start,
                    end=end,
                    dimensions=["device"],
                    search_type=kind,
                    limit=10,
                ),
            )
        now, then = self._totals(now_rows), self._totals(then_rows)
        fresh = self._fresh_from(metadata)
        return GoogleSearchConsoleOverview(
            site_url=url,
            period=self._period_out(start, end),
            compared_with=GoogleSearchConsoleCompare(
                date_from=before_start, date_to=before_end, mode=mode.value
            ),
            search_type=kind,
            totals=now,
            previous_totals=then,
            change={m: _change(m, now.get(m), then.get(m)) for m in METRICS},
            devices={
                row["dimensions"].get("device", ""): row["metrics"] for row in device_rows
            },
            fresh_from=fresh,
            warnings=_fresh_warning(fresh),
        )

    async def search_types(
        self, site: str, *, period: str | None = None
    ) -> GoogleSearchConsoleSearchTypes:
        """The four metrics per search type — web, images, video, news, Discover, Google News.

        Six queries, together. Where the site is *seen* is a question the web total cannot
        answer, and it is the closest the API comes to "which Google surface": AI Overviews sit
        inside ``web`` and cannot be split out (see :meth:`ai_visibility`).
        """
        connection = await self._connection_or_refuse()
        url = site_url(site)
        start, end = await self._window(period)
        async with self._client(connection) as client:
            answers = await asyncio.gather(
                *(
                    self._query(
                        client, url, start=start, end=end, dimensions=[], search_type=kind, limit=1
                    )
                    for kind in SEARCH_TYPES
                )
            )
        fresh = next((self._fresh_from(meta) for _, meta in answers if meta), None)
        return GoogleSearchConsoleSearchTypes(
            site_url=url,
            period=self._period_out(start, end),
            by_type={
                kind: self._totals(rows)
                for kind, (rows, _) in zip(SEARCH_TYPES, answers, strict=True)
            },
            fresh_from=fresh,
        )

    async def timeseries(
        self,
        site: str,
        *,
        period: str | None = None,
        search_type: str | None = None,
        filters: list[str] | None = None,
    ) -> GoogleSearchConsoleReport:
        """Day by day, oldest first — the series a chart is drawn from.

        Google omits a day with no data rather than answering zero for it, so a gap in ``rows``
        is a day nothing was shown, not a day nothing was measured.
        """
        return await self.query(
            site,
            dimensions=["date"],
            period=period,
            search_type=search_type,
            filters=filters,
            limit=MAX_ROWS,
        )

    async def breakdown(
        self,
        site: str,
        *,
        dimension: str,
        period: str | None = None,
        search_type: str | None = None,
        filters: list[str] | None = None,
        limit: int = DEFAULT_ROWS,
        offset: int = 0,
        order: str | None = None,
    ) -> GoogleSearchConsoleReport:
        """One dimension, ranked — top queries, pages, countries, devices, appearances.

        Google ranks by clicks and offers no other sort. Another ``order`` is applied locally
        over the first :data:`ORDER_WINDOW` clicks-ranked rows, and the answer says so: a top-25
        by impressions out of the first thousand by clicks is not the same list as a top-25 by
        impressions, and a caller has to be able to tell which one they got.
        """
        chosen = self._one_of(dimension, FILTER_DIMENSIONS, "dimension", "query")
        wanted = (order or "").strip()
        if not wanted or wanted in ("clicks", "-clicks"):
            # Google's own ranking, paged by Google: the cheap and exact case.
            return await self.query(
                site,
                dimensions=[chosen],
                period=period,
                search_type=search_type,
                filters=filters,
                limit=limit,
                offset=offset,
            )
        by = self._one_of(wanted.lstrip("+-"), METRICS, "order", "clicks")
        descending = not wanted.startswith("+")
        report = await self.query(
            site,
            dimensions=[chosen],
            period=period,
            search_type=search_type,
            filters=filters,
            limit=ORDER_WINDOW,
        )
        rows = sorted(report.rows, key=lambda row: row.metrics.get(by, 0.0), reverse=descending)
        page = rows[max(0, offset) : max(0, offset) + max(1, min(MAX_ROWS, limit))]
        report.rows = page
        report.row_count = len(page)
        report.truncated = len(rows) > max(0, offset) + len(page) or report.truncated
        report.warnings = [*report.warnings, "google_search_console.warning.order_window"]
        return report

    async def hourly(
        self, site: str, *, days: int = 2, search_type: str | None = None
    ) -> GoogleSearchConsoleReport:
        """Hour by hour over the last ``days`` days, today included and still moving.

        The one read that *must* ask for ``hourly_all`` — the ``hour`` dimension answers nothing
        under any other data state — and the one read whose window ends today rather than
        yesterday, because "what happened this morning" is the question it exists for. Google
        keeps ten days of it; asking for more is not refused, it is answered empty, so the
        request is clamped rather than the answer padded.
        """
        connection = await self._connection_or_refuse()
        url = site_url(site)
        kind = self._one_of(search_type, SEARCH_TYPES, "search_type", "web")
        span = max(1, min(HOURLY_DAYS, days))
        today = await self._today()
        start = today - timedelta(days=span - 1)
        async with self._client(connection) as client:
            rows, metadata = await self._query(
                client,
                url,
                start=start,
                end=today,
                dimensions=["hour"],
                search_type=kind,
                data_state="hourly_all",
                limit=span * 24 + 24,
            )
        fresh = self._fresh_from(metadata)
        warnings = _fresh_warning(fresh)
        if (metadata or {}).get("firstIncompleteHour"):
            warnings.append("google_search_console.warning.fresh_hours")
        return GoogleSearchConsoleReport(
            site_url=url,
            period=self._period_out(start, today),
            search_type=kind,
            data_state="hourly_all",
            dimensions=["hour"],
            rows=[GoogleSearchConsoleRow(**row) for row in rows],
            row_count=len(rows),
            fresh_from=fresh,
            warnings=warnings,
        )

    async def movers(
        self,
        site: str,
        *,
        period: str | None = None,
        compare: str | None = None,
        dimension: str = "query",
        limit: int = DEFAULT_ROWS,
        min_impressions: float = DEFAULT_MIN_IMPRESSIONS,
        search_type: str | None = None,
    ) -> GoogleSearchConsoleMovers:
        """Which queries (or pages) moved most in average position between the two spans.

        Two queries, together. A drop in the position *number* is a climb, and the sign is
        normalised so that positive means better — the convention every ranking table on this
        platform already uses, so the SE Ranking tab and this answer read the same way.
        """
        connection = await self._connection_or_refuse()
        url = site_url(site)
        chosen = self._one_of(dimension, ("query", "page"), "dimension", "query")
        kind = self._one_of(search_type, SEARCH_TYPES, "search_type", "web")
        start, end = await self._window(period)
        mode = self._compare_mode(compare)
        before_start, before_end = compare_window(start, end, mode)
        async with self._client(connection) as client:
            (now_rows, metadata), (then_rows, _) = await asyncio.gather(
                self._query(
                    client,
                    url,
                    start=start,
                    end=end,
                    dimensions=[chosen],
                    search_type=kind,
                    limit=ORDER_WINDOW,
                ),
                self._query(
                    client,
                    url,
                    start=before_start,
                    end=before_end,
                    dimensions=[chosen],
                    search_type=kind,
                    limit=ORDER_WINDOW,
                ),
            )
        floor = max(0.0, float(min_impressions))
        now = {
            row["dimensions"].get(chosen, ""): row["metrics"]
            for row in now_rows
            if row["metrics"].get("impressions", 0.0) >= floor
        }
        then = {
            row["dimensions"].get(chosen, ""): row["metrics"]
            for row in then_rows
            if row["metrics"].get("impressions", 0.0) >= floor
        }
        movers: list[GoogleSearchConsoleMover] = []
        for label, current in now.items():
            previous = then.get(label)
            if previous is None or not current.get("position") or not previous.get("position"):
                continue
            movers.append(
                GoogleSearchConsoleMover(
                    label=label,
                    position=round(current["position"], 1),
                    previous_position=round(previous["position"], 1),
                    change=round(previous["position"] - current["position"], 1),
                    clicks=current.get("clicks", 0.0),
                    impressions=current.get("impressions", 0.0),
                )
            )
        movers.sort(key=lambda row: abs(row.change), reverse=True)
        return GoogleSearchConsoleMovers(
            site_url=url,
            period=self._period_out(start, end),
            compared_with=GoogleSearchConsoleCompare(
                date_from=before_start, date_to=before_end, mode=mode.value
            ),
            dimension=chosen,
            min_impressions=floor,
            rows=movers[: max(1, min(MAX_ROWS, limit))],
            entered=len(set(now) - set(then)),
            dropped=len(set(then) - set(now)),
        )

    # --- ask your own question ----------------------------------------------------------------#
    async def query(
        self,
        site: str,
        *,
        dimensions: list[str],
        period: str | None = None,
        search_type: str | None = None,
        filters: list[str] | None = None,
        aggregation: str | None = None,
        data_state: str | None = None,
        limit: int = DEFAULT_ROWS,
        offset: int = 0,
    ) -> GoogleSearchConsoleReport:
        """The escape hatch: any dimensions, any filter, any aggregation, over any period.

        Bounded in the two ways that matter and in no others. The **site is a value this
        connection can already list**, so no query can be aimed at a property the caller could
        not have named — Google refuses the rest with a 403 that this returns as one. And the
        row count is clamped, because the difference between 25 rows and 25 000 is not a
        difference in the question.

        The curated reads above are this same call with a vocabulary chosen for them. The
        permission split (``site.read`` for those, ``report.run`` for this) lives on the routes,
        which is what makes deny-by-default enumerable (§15); the service does not re-decide it.
        """
        connection = await self._connection_or_refuse()
        url = site_url(site)
        chosen_dimensions = [
            self._one_of(name, DIMENSIONS, "dimensions", "query") for name in dimensions
        ]
        if len(set(chosen_dimensions)) != len(chosen_dimensions):
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"dimensions": "errors.validation"},
                details={"dimensions": chosen_dimensions},
            )
        kind = self._one_of(search_type, SEARCH_TYPES, "search_type", "web")
        state = self._one_of(data_state, DATA_STATES, "data_state", "all")
        if "hour" in chosen_dimensions:
            # The hour dimension answers nothing under any other state; a caller who named it
            # meant the fresh hourly rows, so the state follows the dimension rather than 400ing.
            state = "hourly_all"
        aggregate = self._one_of(aggregation, AGGREGATIONS, "aggregation", "auto")
        start, end = await self._window(period)
        size = max(1, min(MAX_ROWS, limit))
        async with self._client(connection) as client:
            rows, metadata = await self._query(
                client,
                url,
                start=start,
                end=end,
                dimensions=chosen_dimensions,
                search_type=kind,
                filters=parse_filters(filters or []),
                aggregation=aggregate,
                data_state=state,
                # One more than is kept, so a full page can say whether it is the whole answer.
                limit=size + 1,
                offset=offset,
            )
        truncated = len(rows) > size
        rows = rows[:size]
        fresh = self._fresh_from(metadata)
        return GoogleSearchConsoleReport(
            site_url=url,
            period=self._period_out(start, end),
            search_type=kind,
            data_state=state,
            dimensions=chosen_dimensions,
            rows=[GoogleSearchConsoleRow(**row) for row in rows],
            row_count=len(rows),
            truncated=truncated,
            fresh_from=fresh,
            warnings=_fresh_warning(fresh),
        )

    # --- the index --------------------------------------------------------------------------- #
    async def inspect(
        self, site: str, *, url: str, language: str | None = None
    ) -> GoogleSearchConsoleInspection:
        """What Google's index holds for one URL under this property.

        The URL Inspection tool as data: is it indexed, why not, which canonical Google chose,
        when it was last crawled. The URL must be under the property, and Google says so with a
        400 that :meth:`_translate` returns as a 422. Its quota is its own — 2 000 inspections
        a day per property — which is why it is one URL per call and never a sweep.
        """
        connection = await self._connection_or_refuse()
        site_value = site_url(site)
        target = str(url or "").strip()
        if not target:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"url": "errors.required"},
            )
        body: dict[str, Any] = {"siteUrl": site_value, "inspectionUrl": target}
        if language:
            body["languageCode"] = language
        async with self._client(connection) as client:
            answer = await post(client, f"{INSPECTION_API}/urlInspection/index:inspect", body)
        result = answer.get("inspectionResult") or {}
        index = result.get("indexStatusResult") or {}
        return GoogleSearchConsoleInspection(
            site_url=site_value,
            inspected_url=target,
            verdict=str(index.get("verdict") or ""),
            coverage_state=str(index.get("coverageState") or ""),
            indexing_state=str(index.get("indexingState") or ""),
            robots_txt_state=str(index.get("robotsTxtState") or ""),
            page_fetch_state=str(index.get("pageFetchState") or ""),
            crawled_as=str(index.get("crawledAs") or ""),
            last_crawl_time=index.get("lastCrawlTime"),
            google_canonical=index.get("googleCanonical"),
            user_canonical=index.get("userCanonical"),
            referring_urls=[str(item) for item in index.get("referringUrls") or []],
            sitemaps=[str(item) for item in index.get("sitemap") or []],
            rich_results=result.get("richResultsResult"),
            mobile_usability=result.get("mobileUsabilityResult"),
            amp=result.get("ampResult"),
            inspection_link=str(result.get("inspectionResultLink") or ""),
        )

    # --- generative AI ----------------------------------------------------------------------- #
    async def ai_visibility(
        self,
        site: str,
        *,
        period: str | None = None,
        compare: str | None = None,
    ) -> GoogleSearchConsoleAiVisibility:
        """How visible the site is in AI Overviews and AI Mode — as far as the API can say.

        Today that is *not at all*, and the answer says so rather than guessing: the Generative
        AI performance report exists in the console (impressions by page, country, device and
        date, from June 2026), and the Search Analytics API's search-type vocabulary has no value
        for it (``client.GENERATIVE_AI_SEARCH_TYPES``, checked against discovery revision
        :data:`API_REVISION_CHECKED`). ``available: False`` plus the report's own URL is the
        honest shape — a tool that answered a plausible number here would be the worst kind of
        wrong, because nothing on any screen could contradict it.

        The credential is still checked first: a caller who cannot reach the property should
        learn that, not be handed a link into an account they are not connected to.
        """
        await self._connection_or_refuse()
        url = site_url(site)
        out = GoogleSearchConsoleAiVisibility(
            site_url=url,
            available=bool(GENERATIVE_AI_SEARCH_TYPES),
            reason=None if GENERATIVE_AI_SEARCH_TYPES else AI_NOT_IN_API,
            report_url=generative_ai_report_url(url),
            api_revision_checked=API_REVISION_CHECKED,
        )
        if not GENERATIVE_AI_SEARCH_TYPES:
            return out
        # The day Google ships the search type, this is the answer: the same overview shape per
        # generative feature, plus the pages the AI features actually linked to.
        connection = await self._connection_or_refuse()
        start, end = await self._window(period)
        mode = self._compare_mode(compare)
        before_start, before_end = compare_window(start, end, mode)
        out.period = self._period_out(start, end)
        out.compared_with = GoogleSearchConsoleCompare(
            date_from=before_start, date_to=before_end, mode=mode.value
        )
        async with self._client(connection) as client:
            for kind in GENERATIVE_AI_SEARCH_TYPES:
                (now_rows, _), (then_rows, _), (page_rows, _) = await asyncio.gather(
                    self._query(
                        client, url, start=start, end=end, dimensions=[], search_type=kind, limit=1
                    ),
                    self._query(
                        client,
                        url,
                        start=before_start,
                        end=before_end,
                        dimensions=[],
                        search_type=kind,
                        limit=1,
                    ),
                    self._query(
                        client,
                        url,
                        start=start,
                        end=end,
                        dimensions=["page"],
                        search_type=kind,
                        limit=10,
                    ),
                )
                now, then = self._totals(now_rows), self._totals(then_rows)
                out.sources[kind] = GoogleSearchConsoleAiSource(
                    totals=now,
                    previous_totals=then,
                    change={m: _change(m, now.get(m), then.get(m)) for m in METRICS},
                    top_pages=[GoogleSearchConsoleRow(**row) for row in page_rows],
                )
        return out


def _change(metric: str, now: Any, then: Any) -> GoogleSearchConsoleChange | None:
    if not isinstance(now, int | float) or not isinstance(then, int | float):
        return None
    absolute = round(now - then, 4)
    return GoogleSearchConsoleChange(
        value_from=then,
        value_to=now,
        absolute=absolute,
        relative=round(absolute / then, 4) if then else None,
        lower_is_better=metric in LOWER_IS_BETTER,
    )


def _fresh_warning(fresh: date | None) -> list[str]:
    return ["google_search_console.warning.fresh_data"] if fresh else []


#: The filter grammar the query string accepts — Google's six operators, one token each.
#: Deliberately no parser beyond this: a query language in a query parameter is a second API
#: nobody documented.
_OPERATORS: tuple[tuple[str, str], ...] = (
    ("==", "equals"),
    ("!=", "notEquals"),
    ("=@", "contains"),
    ("!@", "notContains"),
    ("=~", "includingRegex"),
    ("!~", "excludingRegex"),
)


def parse_filters(raw: list[str]) -> list[dict[str, str]]:
    """``query=@fiets`` / ``country==nld`` / ``page!~/blog/`` → Google's ``filters`` list.

    A malformed clause is **refused**, never dropped: a filter silently ignored answers a
    different question than the one asked with every row valid and the total wrong — the
    SnelStart ``$filter`` lesson (CLAUDE.md), which is the same failure a query string can
    produce here. So is a filter on a dimension Google cannot filter (``date``): refused, with
    the list of ones it can.
    """
    clauses: list[dict[str, str]] = []
    allowed = {item.casefold(): item for item in FILTER_DIMENSIONS}
    for item in raw:
        for token, operator in _OPERATORS:
            field, sep, value = item.partition(token)
            if sep and field.strip() and value.strip():
                dimension = allowed.get(field.strip().casefold())
                if dimension is None:
                    raise AppError(
                        "validation",
                        "errors.validation",
                        status_code=422,
                        fields={"filters": "errors.validation"},
                        details={"filter": item, "allowed": list(FILTER_DIMENSIONS)},
                    )
                clauses.append(
                    {"dimension": dimension, "operator": operator, "expression": value.strip()}
                )
                break
        else:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"filters": "errors.validation"},
                details={"filter": item, "operators": [token for token, _ in _OPERATORS]},
            )
    return clauses
