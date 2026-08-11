"""The read surface: one method per tool, each opening a client and shaping an envelope.

Business-licensed — see LICENSE.

Thin by design. The queries live in :mod:`~app.modules.google_ads.reporting`, the transport in
:mod:`app.core.googleads`, and what is left here is the part every read shares: resolve the
account, open a client with the pooled database connection released, run the read, and wrap the
answer in the envelope that says which account and which span it describes.

**Everything Google-facing happens inside one ``open_client`` block.** Not for tidiness: a
request holds one pooled database connection for its whole transaction, and a Google Ads read of
90 days of search terms genuinely takes seconds. A handful of those held across the call drain
the pool and every other request queues on checkout until ``pool_timeout``, which reads to a user
as the entire site freezing (docs/PERFORMANCE.md). The account row, the developer token and the
connection are all read *before* the release, because nothing inside it may touch the session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from app.core.googleads import AdsClient, format_customer_id, gaql
from app.core.periods import resolve_compare
from app.modules.google_ads import policy as policy_rules
from app.modules.google_ads import reporting
from app.modules.google_ads.decisions import GoogleAdsDecisionService
from app.modules.google_ads.models import GoogleAdsAccount
from app.modules.google_ads.reporting import ReadResult, Window
from app.modules.google_ads.schemas import (
    GoogleAdsAccountBrief,
    GoogleAdsKeywordIdeaRequest,
    GoogleAdsPeriod,
    GoogleAdsQueryRead,
    GoogleAdsQueryRequest,
    GoogleAdsReport,
    GoogleAdsSnapshotRead,
    GoogleAdsTrendRead,
)
from app.modules.google_ads.service import GoogleAdsService
from app.modules.google_ads.trends import read_trend


class GoogleAdsReadService:
    def __init__(self, ctx: Any) -> None:
        self.ctx = ctx
        self.accounts = GoogleAdsService(ctx)

    # --- envelope --------------------------------------------------------------------------- #

    def _brief(self, account: GoogleAdsAccount) -> GoogleAdsAccountBrief:
        return GoogleAdsAccountBrief(
            id=account.id,
            customer_id=account.customer_id,
            customer_id_formatted=format_customer_id(account.customer_id),
            descriptive_name=account.descriptive_name,
            company_id=account.company_id,
        )

    def _envelope(
        self,
        account: GoogleAdsAccount,
        result: ReadResult,
        window: Window | None,
        warnings: list[str],
    ) -> GoogleAdsReport:
        return GoogleAdsReport(
            account=self._brief(account),
            period=(
                GoogleAdsPeriod(
                    date_from=window.start,
                    date_to=window.end,
                    days=window.days,
                    token=window.token,
                )
                if window
                else None
            ),
            currency=account.currency_code,
            account_timezone=account.time_zone,
            # UTC, unlike the change-history timestamps, which are the account's local time.
            # Two clocks in one response is not an accident; pretending otherwise would be.
            fetched_at=datetime.now(UTC),
            row_count=len(result.rows),
            # Deduplicated but order-preserving: a caller reads the first one as the headline.
            warnings=list(dict.fromkeys([*warnings, *result.warnings])),
            totals=result.totals,
            rows=result.rows,
            extra=result.extra,
        )

    async def _window(
        self,
        account: GoogleAdsAccount,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
    ) -> tuple[Window, list[str]]:
        return reporting.resolve_window(
            period=period,
            date_from=date_from,
            date_to=date_to,
            # The **account's** zone, not the org's. Google aggregates a campaign's day in it,
            # so "last month" for an account set to America/New_York is a different set of
            # impressions than last month in Europe/Amsterdam (§8's rule, with the data as the
            # reason this one resolves differently).
            account_timezone=account.time_zone,
        )

    async def _campaign_ids(
        self, client: AdsClient, account: GoogleAdsAccount, names: list[str] | None
    ) -> list[int] | None:
        """Names → ids, so no caller-supplied string ever reaches the GAQL text.

        ``None`` means no filter; ``[]`` means a filter that matched nothing. Collapsing the two
        would turn a typo in a campaign name into a report on the whole account.
        """
        if not names:
            return None
        return await reporting.resolve_campaign_ids(client, account.customer_id, names)

    # --- the reads ---------------------------------------------------------------------------- #

    async def snapshot(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
    ) -> GoogleAdsSnapshotRead:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="snapshot") as (
            client,
            _account,
        ):
            summary = await reporting.read_account(client, account.customer_id, window)
            result = await reporting.read_campaigns(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("campaigns", None),
            )
        envelope = self._envelope(account, result, window, warnings)
        enabled = [row for row in result.rows if row.get("status") == "ENABLED"]
        return GoogleAdsSnapshotRead(
            **envelope.model_dump(),
            account_summary=summary,
            campaign_count=len(result.rows),
            enabled_campaign_count=len(enabled),
            # What the account is committed to spending per day *right now* — the enabled
            # campaigns only, because a paused campaign's budget buys nothing.
            total_daily_budget=round(
                sum(float(row.get("daily_budget") or 0) for row in enabled), 2
            ),
        )

    async def campaigns(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        include_removed: bool = False,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="campaigns") as (
            client,
            _a,
        ):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_campaigns(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("campaigns", limit),
                include_removed=include_removed,
            )
        return self._envelope(account, result, window, warnings)

    async def ad_groups(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        include_removed: bool = False,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="ad_groups") as (
            client,
            _a,
        ):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_ad_groups(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("ad_groups", limit),
                campaign_ids=ids,
                include_removed=include_removed,
            )
        return self._envelope(account, result, window, warnings)

    async def keywords(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        include_removed: bool = False,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="keywords") as (
            client,
            _a,
        ):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_keywords(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("keywords", limit),
                campaign_ids=ids,
                include_removed=include_removed,
            )
        return self._envelope(account, result, window, warnings)

    async def negatives(
        self, account_id: uuid.UUID, *, limit: int | None = None
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        async with self.accounts.open_client(account_id=account_id, tool="negatives") as (
            client,
            _a,
        ):
            result = await reporting.read_negatives(
                client, account.customer_id, limit=reporting.limit_for("negatives", limit)
            )
        # No period: an exclusion is configuration, not a measurement over a span.
        return self._envelope(account, result, None, [])

    async def search_terms(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        min_cost: float | None = None,
        min_clicks: int | None = None,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="search_terms") as (
            client,
            _a,
        ):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_search_terms(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("search_terms", limit),
                campaign_ids=ids,
                min_cost=min_cost,
                min_clicks=min_clicks,
            )
        # After the client block, never inside it: the pooled connection is released for the
        # duration and a query there would re-check one out with no RLS GUC bound, which fails
        # closed rather than erroring. One batched read, not one per row.
        await self._annotate_decisions(account, result.rows)
        return self._envelope(account, result, window, warnings)

    async def _annotate_decisions(self, account: GoogleAdsAccount, rows: list[dict]) -> None:
        """Stamp each search-term row with the decision that already stands about it.

        `match_status` is Google's answer to "is this already a keyword or a negative?" and it is
        silent about the far more common case: somebody looked at this term, decided to keep it,
        and wrote down why. Without that, the same shortlist is produced every month and an
        account manager stops reading it (#300's rule that a report is a record, applied to a
        recommendation).

        Scope-blind on purpose. A term kept in one campaign and excluded in another has two
        standing decisions, and the honest thing to show beside a row that spans campaigns is
        "there is a decision about this term", not one of the two picked arbitrarily.
        """
        if not rows:
            return
        standing = await GoogleAdsDecisionService(self.ctx).standing(
            account.id, subject_type="search_term"
        )
        if not standing:
            return
        by_subject: dict[str, Any] = {}
        for decision in standing.values():
            by_subject.setdefault(decision.subject_key, decision)
        for row in rows:
            found = by_subject.get(policy_rules.normalise(row.get("search_term")))
            row["decided"] = (
                None
                if found is None
                else {
                    "decision": found.decision,
                    "reason": found.reason,
                    "scope": found.scope,
                    "by": found.decided_by,
                }
            )

    async def ads(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="ads") as (client, _a):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_ads(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("ads", limit),
                campaign_ids=ids,
            )
        return self._envelope(account, result, window, warnings)

    async def devices(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="devices") as (
            client,
            _a,
        ):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_devices(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("devices", limit),
                campaign_ids=ids,
            )
        return self._envelope(account, result, window, warnings)

    async def geo(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        campaigns: list[str] | None = None,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="geo") as (client, _a):
            ids = await self._campaign_ids(client, account, campaigns)
            if ids == []:
                return self._empty(account, window, warnings)
            result = await reporting.read_geo(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("geo", limit),
                campaign_ids=ids,
            )
        return self._envelope(account, result, window, warnings)

    async def conversions(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="conversions") as (
            client,
            _a,
        ):
            result = await reporting.read_conversions(
                client,
                account.customer_id,
                window,
                limit=reporting.limit_for("conversions", limit),
            )
        return self._envelope(account, result, window, warnings)

    async def changes(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        limit: int | None = None,
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        async with self.accounts.open_client(account_id=account_id, tool="changes") as (
            client,
            _a,
        ):
            result = await reporting.read_changes(
                client, account.customer_id, window, limit=reporting.limit_for("changes", limit)
            )
        return self._envelope(account, result, window, warnings)

    async def recommendations(
        self, account_id: uuid.UUID, *, limit: int | None = None
    ) -> GoogleAdsReport:
        account = await self.accounts.get_account(account_id)
        async with self.accounts.open_client(account_id=account_id, tool="recommendations") as (
            client,
            _a,
        ):
            result = await reporting.read_recommendations(
                client,
                account.customer_id,
                limit=reporting.limit_for("recommendations", limit),
            )
        return self._envelope(account, result, None, [])

    async def keyword_ideas(
        self, account_id: uuid.UUID, payload: GoogleAdsKeywordIdeaRequest
    ) -> GoogleAdsReport:
        """``:generateKeywordIdeas`` — the one read that is not GAQL.

        It is a customer-scoped custom verb with its own request message, and it is the gap the
        proof-of-concept routed through a third-party SEO tool for want of it.
        """
        from app.errors import AppError

        account = await self.accounts.get_account(account_id)
        seeds = [k.strip() for k in payload.keywords if k and k.strip()]
        if not seeds and not payload.url:
            raise AppError(
                "validation",
                "errors.validation",
                status_code=422,
                fields={"keywords": "errors.google_ads_keyword_seed_required"},
            )
        body: dict[str, Any] = {
            "pageSize": min(payload.limit or 200, 1_000),
            "keywordPlanNetwork": "GOOGLE_SEARCH",
        }
        if seeds and payload.url:
            body["keywordAndUrlSeed"] = {"url": payload.url, "keywords": seeds}
        elif seeds:
            body["keywordSeed"] = {"keywords": seeds}
        else:
            body["urlSeed"] = {"url": payload.url}
        if payload.geo_target_ids:
            body["geoTargetConstants"] = [
                f"geoTargetConstants/{int(gid)}" for gid in payload.geo_target_ids
            ]
        if payload.language_id:
            body["language"] = f"languageConstants/{int(payload.language_id)}"

        async with self.accounts.open_client(account_id=account_id, tool="keyword_ideas") as (
            client,
            _a,
        ):
            payload_out = await client.post(
                account.customer_id, "generateKeywordIdeas", body, context="keyword_ideas"
            )
        rows = []
        for item in payload_out.get("results") or []:
            metrics = item.get("keywordIdeaMetrics") or {}
            rows.append(
                {
                    "keyword": item.get("text") or "",
                    # Google bands this: it is a monthly average over the last 12 months, not a
                    # count of anything that happened.
                    "avg_monthly_searches": reporting._int(metrics.get("avgMonthlySearches")),
                    "competition": metrics.get("competition"),
                    "competition_index": reporting._int(metrics.get("competitionIndex")),
                    "low_top_of_page_bid": reporting.money(metrics.get("lowTopOfPageBidMicros")),
                    "high_top_of_page_bid": reporting.money(
                        metrics.get("highTopOfPageBidMicros")
                    ),
                }
            )
        result = ReadResult(rows=rows, warnings=["google_ads.warning.keyword_volume_is_estimated"])
        return self._envelope(account, result, None, [])

    async def query(
        self, account_id: uuid.UUID, payload: GoogleAdsQueryRequest
    ) -> GoogleAdsQueryRead:
        """The gated passthrough. The guard runs *before* a client is even opened."""
        checked = gaql.check(payload.query, limit=payload.limit)
        account = await self.accounts.get_account(account_id)
        async with self.accounts.open_client(account_id=account_id, tool="query") as (
            client,
            _a,
        ):
            rows = await client.search(
                account.customer_id, checked.query, max_rows=checked.limit, context="query"
            )
        result = ReadResult(rows=rows, warnings=list(checked.warnings))
        envelope = self._envelope(account, result, None, [])
        return GoogleAdsQueryRead(
            **envelope.model_dump(),
            executed_query=checked.query,
            resource=checked.resource,
        )

    async def trend(
        self,
        account_id: uuid.UUID,
        period: str | None,
        date_from: date | None,
        date_to: date | None,
        *,
        compare: str | None = None,
    ) -> GoogleAdsTrendRead:
        """A window against its comparison, from the nightly mirror. **No Google call.**

        Which is the point: a live comparison would be two Ads calls per client per page load
        against a shared daily quota, and the second is for a period whose figures are already
        final. The default comparison is the same period a year earlier — the platform's own
        default (#312), because that is the comparison seasonality survives.
        """
        account = await self.accounts.get_account(account_id)
        window, warnings = await self._window(account, period, date_from, date_to)
        mode = resolve_compare(compare)
        result = await read_trend(
            self.ctx, account_id, start=window.start, end=window.end, mode=mode
        )
        if result.missing_days:
            warnings.append("google_ads.warning.days_not_synced")
        return GoogleAdsTrendRead(
            account=self._brief(account),
            period=GoogleAdsPeriod(
                date_from=result.current_start,
                date_to=result.current_end,
                days=(result.current_end - result.current_start).days + 1,
                token=window.token,
            ),
            compared_with=GoogleAdsPeriod(
                date_from=result.compare_start,
                date_to=result.compare_end,
                days=(result.compare_end - result.compare_start).days + 1,
                token=None,
            ),
            compare_mode=result.compare_mode,
            currency=result.currency or account.currency_code,
            totals=result.totals,
            previous_totals=result.previous_totals,
            change={key: value for key, value in result.change.items() if value is not None},
            series=[{"date": point.day, "metrics": point.metrics} for point in result.series],
            breakdown=result.breakdown,
            missing_days=result.missing_days,
            warnings=warnings,
        )

    def _empty(
        self, account: GoogleAdsAccount, window: Window, warnings: list[str]
    ) -> GoogleAdsReport:
        """A filter that matched no campaign returns nothing — **not** the whole account.

        The distinction ``resolve_campaign_ids`` keeps between ``None`` and ``[]`` only pays off
        if the caller acts on it, and this is where it is acted on.
        """
        result = ReadResult(warnings=["google_ads.warning.no_campaigns_matched"])
        result.totals = reporting.totals_from_rows([])
        return self._envelope(account, result, window, warnings)
