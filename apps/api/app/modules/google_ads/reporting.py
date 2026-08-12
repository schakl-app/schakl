"""What the read surface asks Google, and how the answers are shaped.

Business-licensed — see LICENSE.

Every function here is one GAQL query plus one row shaper. The queries are the valuable part —
they are the questions an agency actually asks, and every field name in them was checked against
the **v25 discovery document** rather than remembered. Three that would have failed silently or
loudly if they had not been:

* ``campaign.start_date_time`` / ``end_date_time`` — **not** ``start_date``. v25 names them
  ``startDateTime``/``endDateTime``, and the shorter name is an ``UNRECOGNIZED_FIELD`` query
  error, not a null.
* ``user_location_view`` carries only ``country_criterion_id``. The country, region and city
  come from **segments** (``segments.geo_target_city`` and friends), which is why the geo read
  selects from two places at once and why its ``granularity`` is a reported fact rather than an
  assumption.
* ``target_cpa`` and ``target_roas`` each live in **two** messages, depending on the bidding
  strategy: ``campaign.target_cpa.target_cpa_micros`` *or*
  ``campaign.maximize_conversions.target_cpa_micros``. Reading one is right for half of an
  agency's campaigns and null for the other half.

Two contracts hold across everything below, and a client that breaks either produces reports
that lie:

1. **Ratios are fractions.** ``ctr`` of ``0.0453`` is 4,53 %. Multiplying happens where it is
   displayed, once.
2. **A non-computable ratio is ``None``, never ``0``.** Zero is a measurement; ``None`` is the
   absence of one, and cost-per-conversion with no conversions is the second. Totals recompute
   their ratios from the summed components rather than averaging the rows' — the average of
   thirty daily CTRs is not the CTR of thirty days.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from app.core.googleads import AdsClient, AdsQueryError
from app.core.periods import period_days, resolve_period
from app.core.timezone import resolve_zoneinfo

#: How far back a read may look. Google keeps far more, but a request that asks for five years of
#: per-keyword rows is a timeout the caller cannot see coming (CLAUDE.md §9: every unbounded read
#: is capped). Matches marketing's own ceiling so the two surfaces cannot disagree about a span.
MAX_RANGE_DAYS = 400

#: Per-read row ceilings. Every list here is *ordered by cost descending* and cut, so the rows
#: that survive are the ones worth looking at — and the cut is **reported** in ``warnings``,
#: because a list of 500 search terms that says nothing looks exactly like an account with 500.
LIMITS: dict[str, int] = {
    "campaigns": 500,
    "ad_groups": 1_000,
    "keywords": 2_000,
    "negatives": 2_000,
    "search_terms": 1_000,
    "ads": 500,
    "devices": 200,
    "geo": 1_000,
    "conversions": 200,
    "changes": 500,
    "recommendations": 200,
}


#: The row keys a free-text search looks at, per read.
#:
#: Declared rather than derived from the row, for two reasons that both produce a search nobody
#: can reason about. A sweep over *every* key matches ids, resource names and micro amounts
#: nobody typed — looking for the campaign "1" would return every campaign whose id holds a one.
#: And an **enum column is deliberately absent**, even where a table draws it: the browser
#: renders those through ``google_ads.enum.*``, so a Dutch reader looking at "Gepauzeerd" would
#: be searching a row that stores ``PAUSED``, and the search would fail for exactly the word in
#: front of them. What a closed vocabulary needs is a filter, which is what ``Slice.status`` is.
#:
#: So: the fields whose printed text *is* their stored text, and nothing else.
SEARCH_FIELDS: dict[str, tuple[str, ...]] = {
    "campaigns": ("campaign_name",),
    "ad_groups": ("ad_group_name", "campaign_name"),
    "keywords": ("keyword", "campaign_name", "ad_group_name"),
    "negatives": ("keyword", "campaign_name", "ad_group_name", "shared_set_name"),
    "search_terms": ("search_term", "campaign_name", "ad_group_name"),
    "ads": ("campaign_name", "ad_group_name", "final_urls"),
    "devices": ("campaign_name",),
    "geo": ("country", "region", "city", "campaign_name"),
    "conversions": ("name",),
    "changes": ("changed_by", "changed_resource"),
    "recommendations": ("campaign", "ad_group"),
}


@dataclass(frozen=True)
class Slice:
    """Which part of a read's answer the caller wants: a filter over it, and a page of that.

    Carried as one object rather than four parameters because the **order they are applied in**
    is the whole correctness argument, and that order lives in exactly one place
    (:meth:`ReadResult.narrow`). A read that filtered its page instead of paging its filter would
    be wrong in a way nothing on the screen could reveal.
    """

    #: Free text, matched case-insensitively against :data:`SEARCH_FIELDS` for the read.
    search: str | None = None
    #: A Google status name (``ENABLED``, ``PAUSED``, ``REMOVED``). Only offered by the reads
    #: whose rows carry one — asking it of a list that has no status would silently answer none.
    status: str | None = None
    #: Where the page starts in the matched set.
    offset: int = 0
    #: How many rows the page holds. ``None`` is "the rest", which is what a caller with no pager
    #: means and what every MCP tool meant before there was one.
    limit: int | None = None


def limit_for(kind: str, requested: int | None) -> int:
    """The smaller of what the caller asked for and what the read is allowed to return."""
    ceiling = LIMITS.get(kind, 500)
    if requested is None or requested <= 0:
        return ceiling
    return min(requested, ceiling)


@dataclass(frozen=True)
class Window:
    """The span a read covers, resolved in the **account's** timezone."""

    start: date
    end: date
    #: The token that produced it, echoed so a caller can tell a named month from a trailing
    #: window that happens to cover the same days.
    token: str | None = None

    @property
    def days(self) -> int:
        return period_days(self.start, self.end)

    def gaql(self) -> str:
        return f"segments.date BETWEEN '{self.start.isoformat()}' AND '{self.end.isoformat()}'"


def resolve_window(
    *,
    period: str | None,
    date_from: date | None,
    date_to: date | None,
    account_timezone: str | None,
) -> tuple[Window, list[str]]:
    """The span to read, and anything the caller should know about how it was decided.

    **The clock is the advertising account's, not the org's**, and that is not a detail: Google
    aggregates a campaign's day in the account's own timezone, so "yesterday" for an account set
    to America/New_York is a different set of impressions than yesterday in Europe/Amsterdam.
    Every other wall-clock question in this codebase resolves against the org (§8); this one is
    the exception, and it is the exception because the *data* is.

    Explicit dates win over a period token — a caller who names two dates means them. An
    unparseable token falls back rather than 422ing, for the reason §10 gives: a period arrives
    from a query string anyone can edit and an old bookmark can carry.
    """
    warnings: list[str] = []
    if date_from and date_to:
        if date_from > date_to:
            date_from, date_to = date_to, date_from
            warnings.append("google_ads.warning.dates_swapped")
        span = period_days(date_from, date_to)
        if span > MAX_RANGE_DAYS:
            date_from = date_to - _days(MAX_RANGE_DAYS - 1)
            warnings.append("google_ads.warning.range_capped")
        return Window(start=date_from, end=date_to), warnings

    today = datetime.now(resolve_zoneinfo(account_timezone)).date()
    start, end = resolve_period(period, today, max_days=MAX_RANGE_DAYS)
    if end >= today:
        # Google's own numbers for today are provisional and conversions keep arriving for days
        # afterwards. `resolve_period` already ends yesterday; this is the belt for a caller who
        # passed explicit dates through a path that reaches here.
        warnings.append("google_ads.warning.period_not_final")
    return Window(start=start, end=end, token=period), warnings


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


# --- value coercion -------------------------------------------------------------------------- #


def _int(raw: Any) -> int:
    """An int64 out of Google's JSON, where it arrives as a **string**.

    ``int(raw)`` on the string is right; ``raw or 0`` is not, and neither is treating it as a
    number — a client that concatenates two "impressions" instead of adding them produces a
    plausible-looking figure nobody can reproduce.
    """
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _float(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _optional_float(raw: Any) -> float | None:
    """``None`` stays ``None``. Google omits an absent optional entirely, which is the whole
    reason ``null ≠ 0`` costs nothing to honour here."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def money(micros: Any) -> float:
    """Micros → the account's currency, two decimals. Ads reports every amount this way."""
    return round(_int(micros) / 1_000_000, 2)


def _ratio(numerator: float, denominator: float) -> float | None:
    """A fraction, or ``None`` when the denominator makes it meaningless.

    Six decimals because a CTR of 0.0453 is 4,53 % and a report wants a tenth of a percent.
    """
    if not denominator:
        return None
    return round(numerator / denominator, 6)


# --- the metrics block ----------------------------------------------------------------------- #

#: The metric fields every performance read selects. One list, so a row from the campaign read
#: and a row from the keyword read carry the same keys and a caller learns the shape once.
METRIC_FIELDS = (
    "metrics.impressions",
    "metrics.clicks",
    "metrics.cost_micros",
    "metrics.conversions",
    "metrics.conversions_value",
    "metrics.all_conversions",
)

#: Impression share, which only Search-like campaigns report. Selected separately because on a
#: Display or Video campaign the fields come back **absent**, and absent is not 0 % visibility.
IMPRESSION_SHARE_FIELDS = (
    "metrics.search_impression_share",
    "metrics.search_budget_lost_impression_share",
    "metrics.search_rank_lost_impression_share",
)


def metrics_block(row: dict[str, Any]) -> dict[str, Any]:
    """The shared metric shape, with every ratio derived here rather than trusted from Google.

    Google *does* send ``ctr``, ``averageCpc`` and ``costPerConversion``, and they are correct
    per row. They are recomputed anyway so that a row and a total are produced by the same code:
    otherwise summing rows and reading Google's per-row ratio give two different answers for the
    same question and only one of them is ever wrong at a time.
    """
    m = row.get("metrics", {})
    impressions = _int(m.get("impressions"))
    clicks = _int(m.get("clicks"))
    cost = money(m.get("costMicros"))
    conversions = round(_float(m.get("conversions")), 2)
    conversions_value = round(_float(m.get("conversionsValue")), 2)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "cost": cost,
        "conversions": conversions,
        "conversions_value": conversions_value,
        "all_conversions": round(_float(m.get("allConversions")), 2),
        "ctr": _ratio(clicks, impressions),
        "average_cpc": round(cost / clicks, 2) if clicks else None,
        "conversion_rate": _ratio(conversions, clicks),
        "cost_per_conversion": round(cost / conversions, 2) if conversions else None,
        "value_per_conversion": round(conversions_value / conversions, 2) if conversions else None,
    }


def impression_share_block(row: dict[str, Any]) -> dict[str, Any]:
    """Fractions, and ``None`` where the campaign type does not report them at all."""
    m = row.get("metrics", {})
    return {
        "search_impression_share": _optional_float(m.get("searchImpressionShare")),
        "search_lost_is_budget": _optional_float(m.get("searchBudgetLostImpressionShare")),
        "search_lost_is_rank": _optional_float(m.get("searchRankLostImpressionShare")),
    }


#: Metrics that add. Everything else in the block is derived and must be **recomputed** from
#: these, never averaged — the mistake this tuple exists to make impossible.
_ADDITIVE = ("impressions", "clicks", "cost", "conversions", "conversions_value", "all_conversions")


def totals_from_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """One metrics block over many, with the ratios re-derived from the summed components."""
    total = {key: 0.0 for key in _ADDITIVE}
    for row in rows:
        for key in _ADDITIVE:
            total[key] += float(row.get(key) or 0)
    impressions = int(total["impressions"])
    clicks = int(total["clicks"])
    cost = round(total["cost"], 2)
    conversions = round(total["conversions"], 2)
    conversions_value = round(total["conversions_value"], 2)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "cost": cost,
        "conversions": conversions,
        "conversions_value": conversions_value,
        "all_conversions": round(total["all_conversions"], 2),
        "ctr": _ratio(clicks, impressions),
        "average_cpc": round(cost / clicks, 2) if clicks else None,
        "conversion_rate": _ratio(conversions, clicks),
        "cost_per_conversion": round(cost / conversions, 2) if conversions else None,
        "value_per_conversion": round(conversions_value / conversions, 2) if conversions else None,
    }


# --- shared query pieces ----------------------------------------------------------------------- #


def _select(*groups: tuple[str, ...] | list[str]) -> str:
    fields: list[str] = []
    for group in groups:
        fields.extend(group)
    return ", ".join(dict.fromkeys(fields))


def _status_filter(resource: str, include_removed: bool) -> str:
    """Google keeps removed entities forever and reports their historical spend.

    Excluded by default because a keyword list where a third of the rows cannot be acted on is
    a worse answer to "what are we bidding on"; included on request, because "what did we spend
    on the keywords we since removed" is a real question.
    """
    return "" if include_removed else f" AND {resource}.status != 'REMOVED'"


@dataclass
class ReadResult:
    """Rows plus everything the caller must know before drawing a conclusion from them."""

    rows: list[dict[str, Any]] = field(default_factory=list)
    totals: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)
    #: Per-read extras (``granularity`` for geo, ``effective_period`` for changes).
    extra: dict[str, Any] = field(default_factory=dict)
    #: How many rows matched **before** a page was taken. ``None`` until :meth:`narrow` has run,
    #: which is how the envelope tells "nobody asked for a page" from "the page is empty".
    total_rows: int | None = None

    def truncated(self, limit: int, key: str = "google_ads.warning.rows_truncated") -> ReadResult:
        """Cut to ``limit`` and *say so*. Never a silent prefix (CLAUDE.md §17)."""
        if len(self.rows) > limit:
            self.rows = self.rows[:limit]
            self.warnings.append(key)
        return self

    def narrow(self, view: Slice, *, kind: str) -> ReadResult:
        """Apply the caller's filter and take their page, in the one order that does not lie.

        **Filter, then total, then slice.** Every other order is wrong in a way nothing on the
        screen can reveal. Filtering the page searches a prefix — the sample-of-itself failure
        CLAUDE.md §9 exists to prevent, one layer in from the URL. Totalling the page prints
        "Totaal" under page 3 of 12 over a footer that describes fifty rows out of nine hundred.
        And counting after the slice makes the pager say "1 tot 50 van 50" on every page.

        The search runs **here, in Python, never as a GAQL literal**. That is the same rule
        ``resolve_campaign_ids`` follows for the campaign filter: no caller-supplied string
        reaches the query text. It costs nothing extra, because the fetch is the read's own
        ceiling either way — and it sees the whole fetched set, with :meth:`truncated` having
        already said so if Google had more than that.
        """
        wanted = (view.status or "").strip().upper()
        if wanted:
            self.rows = [row for row in self.rows if str(row.get("status") or "").upper() == wanted]
        needle = (view.search or "").strip().casefold()
        if needle:
            fields = SEARCH_FIELDS.get(kind, ())
            self.rows = [row for row in self.rows if _matches(row, fields, needle)]
        if (wanted or needle) and self.totals is not None:
            # The footer describes the list above it, so a filtered list gets filtered totals.
            self.totals = totals_from_rows(self.rows)
        self.total_rows = len(self.rows)
        if view.offset or view.limit is not None:
            end = None if view.limit is None else view.offset + view.limit
            self.rows = self.rows[view.offset : end]
        return self


def _matches(row: dict[str, Any], fields: tuple[str, ...], needle: str) -> bool:
    """Does any of ``fields`` on this row contain ``needle``, case-insensitively?

    A list value (an ad's ``final_urls``) matches on any of its entries: what the table draws is
    the list, so searching it row-wise is what a reader means.
    """
    for name in fields:
        value = row.get(name)
        if isinstance(value, str):
            if needle in value.casefold():
                return True
        elif isinstance(value, list) and any(needle in str(item).casefold() for item in value):
            return True
    return False


# --- the reads --------------------------------------------------------------------------------- #


async def read_campaigns(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    include_removed: bool = False,
) -> ReadResult:
    """Campaign performance, settings and impression share — the richest row in the module."""
    query = (
        "SELECT "
        + _select(
            (
                "campaign.id",
                "campaign.name",
                "campaign.status",
                "campaign.serving_status",
                "campaign.advertising_channel_type",
                "campaign.advertising_channel_sub_type",
                "campaign.bidding_strategy_type",
                # v25 names these `startDateTime`/`endDateTime`; `campaign.start_date` is an
                # UNRECOGNIZED_FIELD, not a null.
                "campaign.start_date_time",
                "campaign.end_date_time",
                "campaign.optimization_score",
                "campaign_budget.amount_micros",
                "campaign_budget.delivery_method",
                # Both homes of each bidding target: which one is populated depends on the
                # strategy, and reading one is null for half an agency's campaigns.
                "campaign.target_cpa.target_cpa_micros",
                "campaign.maximize_conversions.target_cpa_micros",
                "campaign.target_roas.target_roas",
                "campaign.maximize_conversion_value.target_roas",
            ),
            METRIC_FIELDS,
            IMPRESSION_SHARE_FIELDS,
        )
        + " FROM campaign"
        + f" WHERE {window.gaql()}"
        + _status_filter("campaign", include_removed)
        + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
    )
    rows = await client.search(customer_id, query, context="campaigns")
    result = ReadResult(rows=[_campaign_row(row) for row in rows]).truncated(limit)
    result.totals = totals_from_rows(result.rows)
    return result


def _campaign_row(row: dict[str, Any]) -> dict[str, Any]:
    campaign = row.get("campaign", {})
    budget = row.get("campaignBudget", {})
    return {
        # A string, deliberately: an Ads id is an int64 and JSON numbers lose precision above
        # 2^53. Google sends it as a string for the same reason; keeping it one is the fix.
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": campaign.get("name") or "",
        "status": campaign.get("status"),
        "serving_status": campaign.get("servingStatus"),
        "channel_type": campaign.get("advertisingChannelType"),
        "channel_sub_type": campaign.get("advertisingChannelSubType"),
        "bidding_strategy_type": campaign.get("biddingStrategyType"),
        "start_date_time": campaign.get("startDateTime"),
        "end_date_time": campaign.get("endDateTime"),
        "optimization_score": _optional_float(campaign.get("optimizationScore")),
        "daily_budget": money(budget.get("amountMicros")) if budget else None,
        "budget_delivery_method": budget.get("deliveryMethod"),
        "target_cpa": _bidding_cpa(campaign),
        "target_roas": _bidding_roas(campaign),
        **metrics_block(row),
        **impression_share_block(row),
    }


def _bidding_cpa(campaign: dict[str, Any]) -> float | None:
    """The target CPA, from whichever of the two messages carries it."""
    for key in ("targetCpa", "maximizeConversions"):
        micros = (campaign.get(key) or {}).get("targetCpaMicros")
        if micros is not None:
            return money(micros)
    return None


def _bidding_roas(campaign: dict[str, Any]) -> float | None:
    for key in ("targetRoas", "maximizeConversionValue"):
        value = (campaign.get(key) or {}).get("targetRoas")
        if value is not None:
            return round(_float(value), 4)
    return None


async def read_ad_groups(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    campaign_ids: list[int] | None = None,
    include_removed: bool = False,
) -> ReadResult:
    query = (
        "SELECT "
        + _select(
            (
                "campaign.id",
                "campaign.name",
                "ad_group.id",
                "ad_group.name",
                "ad_group.status",
                "ad_group.type",
                "ad_group.cpc_bid_micros",
            ),
            METRIC_FIELDS,
        )
        + " FROM ad_group"
        + f" WHERE {window.gaql()}"
        + _campaign_filter(campaign_ids)
        + _status_filter("ad_group", include_removed)
        + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
    )
    rows = await client.search(customer_id, query, context="ad_groups")
    result = ReadResult(rows=[_ad_group_row(row) for row in rows]).truncated(limit)
    result.totals = totals_from_rows(result.rows)
    return result


def _ad_group_row(row: dict[str, Any]) -> dict[str, Any]:
    campaign = row.get("campaign", {})
    ad_group = row.get("adGroup", {})
    return {
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": campaign.get("name") or "",
        "ad_group_id": str(ad_group.get("id") or ""),
        "ad_group_name": ad_group.get("name") or "",
        "status": ad_group.get("status"),
        "ad_group_type": ad_group.get("type"),
        "cpc_bid": money(ad_group.get("cpcBidMicros")) if ad_group.get("cpcBidMicros") else None,
        **metrics_block(row),
    }


def _campaign_filter(campaign_ids: list[int] | None) -> str:
    """A campaign filter as **integers**, never as a name.

    Names never enter GAQL: a caller filters by name through ``resolve_campaign_ids`` below,
    which translates once against the account and leaves only numbers in the query text.
    """
    if not campaign_ids:
        return ""
    ids = ", ".join(str(int(cid)) for cid in campaign_ids)
    return f" AND campaign.id IN ({ids})"


async def resolve_campaign_ids(
    client: AdsClient, customer_id: str, names: list[str]
) -> list[int] | None:
    """Campaign names → ids, case-insensitively, by substring.

    Returns ``None`` for an empty ask (meaning *no filter*) and ``[]`` when nothing matched
    (meaning *explicitly nothing*). The difference is load-bearing: collapsing them makes a
    filter that matches no campaign silently return the whole account.
    """
    wanted = [n.strip().casefold() for n in names if n and n.strip()]
    if not wanted:
        return None
    rows = await client.search(
        customer_id,
        "SELECT campaign.id, campaign.name FROM campaign LIMIT 2000",
        context="resolve_campaigns",
    )
    out: list[int] = []
    for row in rows:
        campaign = row.get("campaign", {})
        name = (campaign.get("name") or "").casefold()
        if any(needle in name for needle in wanted):
            out.append(int(campaign.get("id")))
    return out


async def read_keywords(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    campaign_ids: list[int] | None = None,
    include_removed: bool = False,
) -> ReadResult:
    """Positive keywords with their match type, bid and Quality Score components.

    ``FROM keyword_view`` rather than ``ad_group_criterion``: the view is the one that carries
    metrics, and Quality Score rides along on the criterion attributes either way.
    """
    query = (
        "SELECT "
        + _select(
            (
                "campaign.id",
                "campaign.name",
                "ad_group.id",
                "ad_group.name",
                "ad_group_criterion.criterion_id",
                "ad_group_criterion.keyword.text",
                "ad_group_criterion.keyword.match_type",
                "ad_group_criterion.status",
                "ad_group_criterion.cpc_bid_micros",
                "ad_group_criterion.effective_cpc_bid_micros",
                "ad_group_criterion.quality_info.quality_score",
                "ad_group_criterion.quality_info.creative_quality_score",
                "ad_group_criterion.quality_info.post_click_quality_score",
                "ad_group_criterion.quality_info.search_predicted_ctr",
            ),
            METRIC_FIELDS,
        )
        + " FROM keyword_view"
        + f" WHERE {window.gaql()}"
        + _campaign_filter(campaign_ids)
        + _status_filter("ad_group_criterion", include_removed)
        + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
    )
    rows = await client.search(customer_id, query, context="keywords")
    result = ReadResult(rows=[_keyword_row(row) for row in rows]).truncated(limit)
    result.totals = totals_from_rows(result.rows)
    return result


def _keyword_row(row: dict[str, Any]) -> dict[str, Any]:
    campaign = row.get("campaign", {})
    ad_group = row.get("adGroup", {})
    criterion = row.get("adGroupCriterion", {})
    keyword = criterion.get("keyword", {})
    quality = criterion.get("qualityInfo", {})
    return {
        "campaign_id": str(campaign.get("id") or ""),
        "campaign_name": campaign.get("name") or "",
        "ad_group_id": str(ad_group.get("id") or ""),
        "ad_group_name": ad_group.get("name") or "",
        "criterion_id": str(criterion.get("criterionId") or ""),
        "keyword": keyword.get("text") or "",
        "match_type": keyword.get("matchType"),
        "status": criterion.get("status"),
        "cpc_bid": money(criterion.get("cpcBidMicros")) if criterion.get("cpcBidMicros") else None,
        "effective_cpc_bid": (
            money(criterion.get("effectiveCpcBidMicros"))
            if criterion.get("effectiveCpcBidMicros")
            else None
        ),
        # An absent Quality Score is not a zero: Google reports none until a keyword has enough
        # impressions to have one, and a 0 on screen would read as the worst possible score.
        "quality_score": quality.get("qualityScore"),
        "creative_quality": quality.get("creativeQualityScore"),
        "landing_page_quality": quality.get("postClickQualityScore"),
        "expected_ctr": quality.get("searchPredictedCtr"),
        **metrics_block(row),
    }


async def read_negatives(
    client: AdsClient, customer_id: str, *, limit: int
) -> ReadResult:
    """Every way a term can be excluded, in one answer.

    Three separate resources, because Google models them as three things and an agency thinks of
    them as one question ("what are we blocking?"). Ad-group and campaign negatives are
    criteria; a shared negative list is a ``shared_set`` whose members are ``shared_criterion``
    rows, attached to campaigns by ``campaign_shared_set``. A tool that answered only the first
    would miss the list most agencies actually maintain.

    No date range and no metrics: an exclusion is configuration. It either exists or it does not.
    """
    warnings: list[str] = []
    rows: list[dict[str, Any]] = []

    ad_group_rows = await client.search(
        customer_id,
        "SELECT campaign.id, campaign.name, ad_group.id, ad_group.name, "
        "ad_group_criterion.criterion_id, ad_group_criterion.keyword.text, "
        "ad_group_criterion.keyword.match_type "
        "FROM ad_group_criterion "
        "WHERE ad_group_criterion.negative = TRUE "
        "AND ad_group_criterion.type = 'KEYWORD' "
        f"AND ad_group_criterion.status != 'REMOVED' LIMIT {limit + 1}",
        context="negatives_ad_group",
    )
    for row in ad_group_rows:
        criterion = row.get("adGroupCriterion", {})
        keyword = criterion.get("keyword", {})
        rows.append(
            {
                "level": "ad_group",
                "keyword": keyword.get("text") or "",
                "match_type": keyword.get("matchType"),
                "criterion_id": str(criterion.get("criterionId") or ""),
                "campaign_id": str(row.get("campaign", {}).get("id") or ""),
                "campaign_name": row.get("campaign", {}).get("name") or "",
                "ad_group_id": str(row.get("adGroup", {}).get("id") or ""),
                "ad_group_name": row.get("adGroup", {}).get("name") or "",
                "shared_set_name": None,
            }
        )

    campaign_rows = await client.search(
        customer_id,
        "SELECT campaign.id, campaign.name, campaign_criterion.criterion_id, "
        "campaign_criterion.keyword.text, campaign_criterion.keyword.match_type "
        "FROM campaign_criterion "
        "WHERE campaign_criterion.negative = TRUE "
        "AND campaign_criterion.type = 'KEYWORD' "
        f"AND campaign_criterion.status != 'REMOVED' LIMIT {limit + 1}",
        context="negatives_campaign",
    )
    for row in campaign_rows:
        criterion = row.get("campaignCriterion", {})
        keyword = criterion.get("keyword", {})
        rows.append(
            {
                "level": "campaign",
                "keyword": keyword.get("text") or "",
                "match_type": keyword.get("matchType"),
                "criterion_id": str(criterion.get("criterionId") or ""),
                "campaign_id": str(row.get("campaign", {}).get("id") or ""),
                "campaign_name": row.get("campaign", {}).get("name") or "",
                "ad_group_id": None,
                "ad_group_name": None,
                "shared_set_name": None,
            }
        )

    shared_rows = await client.search(
        customer_id,
        "SELECT shared_set.id, shared_set.name, shared_criterion.criterion_id, "
        "shared_criterion.keyword.text, shared_criterion.keyword.match_type "
        "FROM shared_criterion "
        "WHERE shared_set.type = 'NEGATIVE_KEYWORDS' "
        f"AND shared_set.status != 'REMOVED' LIMIT {limit + 1}",
        context="negatives_shared",
    )
    for row in shared_rows:
        criterion = row.get("sharedCriterion", {})
        keyword = criterion.get("keyword", {})
        rows.append(
            {
                "level": "shared_set",
                "keyword": keyword.get("text") or "",
                "match_type": keyword.get("matchType"),
                "criterion_id": str(criterion.get("criterionId") or ""),
                "campaign_id": None,
                "campaign_name": None,
                "ad_group_id": None,
                "ad_group_name": None,
                "shared_set_name": row.get("sharedSet", {}).get("name") or "",
            }
        )

    result = ReadResult(rows=rows, warnings=warnings).truncated(limit)
    result.extra["levels"] = ["ad_group", "campaign", "shared_set"]
    return result


async def read_search_terms(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    campaign_ids: list[int] | None = None,
    min_cost: float | None = None,
    min_clicks: int | None = None,
) -> ReadResult:
    """What people actually typed, most expensive first.

    ``search_term_view.status`` is the *match status* — whether the term is already added as a
    keyword, already excluded, both, or neither. It is the field that turns a list of terms into
    a list of decisions still to make.
    """
    conditions = [window.gaql()]
    if min_cost:
        conditions.append(f"metrics.cost_micros >= {int(round(min_cost * 1_000_000))}")
    if min_clicks:
        conditions.append(f"metrics.clicks >= {int(min_clicks)}")
    query = (
        "SELECT "
        + _select(
            (
                "search_term_view.search_term",
                "search_term_view.status",
                "campaign.id",
                "campaign.name",
                "ad_group.id",
                "ad_group.name",
                "segments.search_term_match_type",
            ),
            METRIC_FIELDS,
        )
        + " FROM search_term_view"
        + " WHERE "
        + " AND ".join(conditions)
        + _campaign_filter(campaign_ids)
        + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
    )
    rows = await client.search(customer_id, query, context="search_terms")
    result = ReadResult(rows=[_search_term_row(row) for row in rows]).truncated(limit)
    result.totals = totals_from_rows(result.rows)
    # Stated on every call, because the alternative is an agent treating this list as a verdict.
    result.warnings.append("google_ads.warning.search_terms_unclassified")
    return result


def _search_term_row(row: dict[str, Any]) -> dict[str, Any]:
    view = row.get("searchTermView", {})
    return {
        "search_term": view.get("searchTerm") or "",
        # ADDED / EXCLUDED / ADDED_EXCLUDED / NONE — the decision already taken, if any.
        "match_status": view.get("status"),
        "match_type": row.get("segments", {}).get("searchTermMatchType"),
        "campaign_id": str(row.get("campaign", {}).get("id") or ""),
        "campaign_name": row.get("campaign", {}).get("name") or "",
        "ad_group_id": str(row.get("adGroup", {}).get("id") or ""),
        "ad_group_name": row.get("adGroup", {}).get("name") or "",
        **metrics_block(row),
    }


async def read_ads(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    campaign_ids: list[int] | None = None,
) -> ReadResult:
    query = (
        "SELECT "
        + _select(
            (
                "campaign.id",
                "campaign.name",
                "ad_group.id",
                "ad_group.name",
                "ad_group_ad.ad.id",
                "ad_group_ad.ad.type",
                "ad_group_ad.ad.final_urls",
                "ad_group_ad.status",
                "ad_group_ad.ad_strength",
                "ad_group_ad.policy_summary.approval_status",
            ),
            METRIC_FIELDS,
        )
        + " FROM ad_group_ad"
        + f" WHERE {window.gaql()}"
        + _campaign_filter(campaign_ids)
        + " AND ad_group_ad.status != 'REMOVED'"
        + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
    )
    rows = await client.search(customer_id, query, context="ads")
    result = ReadResult(rows=[_ad_row(row) for row in rows]).truncated(limit)
    result.totals = totals_from_rows(result.rows)
    return result


def _ad_row(row: dict[str, Any]) -> dict[str, Any]:
    ad_group_ad = row.get("adGroupAd", {})
    ad = ad_group_ad.get("ad", {})
    return {
        "campaign_id": str(row.get("campaign", {}).get("id") or ""),
        "campaign_name": row.get("campaign", {}).get("name") or "",
        "ad_group_id": str(row.get("adGroup", {}).get("id") or ""),
        "ad_group_name": row.get("adGroup", {}).get("name") or "",
        "ad_id": str(ad.get("id") or ""),
        "ad_type": ad.get("type"),
        "final_urls": ad.get("finalUrls") or [],
        "status": ad_group_ad.get("status"),
        "ad_strength": ad_group_ad.get("adStrength"),
        "approval_status": (ad_group_ad.get("policySummary") or {}).get("approvalStatus"),
        **metrics_block(row),
    }


async def read_devices(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    campaign_ids: list[int] | None = None,
) -> ReadResult:
    """Per device, per campaign — plus an account-wide rollup.

    Both, because they answer different questions: the rollup says "mobile costs us more per
    conversion", the per-campaign rows say which campaign is responsible. A large CPA gap
    between devices *inside one campaign* is the strongest signal this read produces.
    """
    query = (
        "SELECT "
        + _select(
            ("campaign.id", "campaign.name", "campaign.status", "segments.device"),
            METRIC_FIELDS,
        )
        + " FROM campaign"
        + f" WHERE {window.gaql()}"
        + _campaign_filter(campaign_ids)
        + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
    )
    rows = await client.search(customer_id, query, context="devices")
    shaped = [
        {
            "campaign_id": str(row.get("campaign", {}).get("id") or ""),
            "campaign_name": row.get("campaign", {}).get("name") or "",
            "campaign_status": row.get("campaign", {}).get("status"),
            "device": row.get("segments", {}).get("device"),
            **metrics_block(row),
        }
        for row in rows
    ]
    result = ReadResult(rows=shaped).truncated(limit)
    by_device: dict[str, list[dict[str, Any]]] = {}
    for row in result.rows:
        by_device.setdefault(row["device"] or "UNKNOWN", []).append(row)
    result.extra["device_totals"] = sorted(
        ({"device": device, **totals_from_rows(rows)} for device, rows in by_device.items()),
        key=lambda item: item["cost"],
        reverse=True,
    )
    result.totals = totals_from_rows(result.rows)
    return result


async def read_geo(
    client: AdsClient,
    customer_id: str,
    window: Window,
    *,
    limit: int,
    campaign_ids: list[int] | None = None,
) -> ReadResult:
    """Where the people who saw the ads actually were.

    ``user_location_view`` is the **physical** location of the user, not the targeting setting —
    which is the point: traffic from outside the targeted area is exactly what this read exists
    to surface, and a targeting-based report can never show it.

    The resource itself carries only ``country_criterion_id``; the country, region and city come
    from segments. Not every account can segment by region and city, so a refusal is caught and
    the read falls back to country level with ``granularity`` saying so — **check that field
    before writing a region into a report**.
    """
    fine = (
        "segments.geo_target_city",
        "segments.geo_target_region",
        "segments.geo_target_country",
    )
    coarse = ("segments.geo_target_country",)
    warnings: list[str] = []
    granularity = "country+region+city"

    def build(segments: tuple[str, ...]) -> str:
        return (
            "SELECT "
            + _select(
                ("campaign.id", "campaign.name", "user_location_view.country_criterion_id"),
                segments,
                METRIC_FIELDS,
            )
            + " FROM user_location_view"
            + f" WHERE {window.gaql()}"
            + _campaign_filter(campaign_ids)
            + f" ORDER BY metrics.cost_micros DESC LIMIT {limit + 1}"
        )

    try:
        rows = await client.search(customer_id, build(fine), context="geo")
    except AdsQueryError:
        # Control flow, not an error: some accounts cannot select city/region at all, and the
        # honest answer is country-level data plus a label saying that is what it is.
        granularity = "country"
        warnings.append("google_ads.warning.geo_country_only")
        rows = await client.search(customer_id, build(coarse), context="geo_country")

    names = await _geo_names(client, customer_id, rows)
    shaped = []
    for row in rows:
        segments = row.get("segments", {})
        shaped.append(
            {
                "campaign_id": str(row.get("campaign", {}).get("id") or ""),
                "campaign_name": row.get("campaign", {}).get("name") or "",
                "country": names.get(segments.get("geoTargetCountry", "")),
                "region": names.get(segments.get("geoTargetRegion", "")),
                "city": names.get(segments.get("geoTargetCity", "")),
                "country_criterion_id": str(
                    row.get("userLocationView", {}).get("countryCriterionId") or ""
                ),
                **metrics_block(row),
            }
        )
    result = ReadResult(rows=shaped, warnings=warnings).truncated(limit)
    result.totals = totals_from_rows(result.rows)
    result.extra["granularity"] = granularity
    result.extra["location_basis"] = "user_location"
    return result


async def _geo_names(
    client: AdsClient, customer_id: str, rows: list[dict[str, Any]]
) -> dict[str, str]:
    """Resolve ``geoTargetConstants/1010`` resource names to readable places, in one query.

    One batched lookup rather than one per row: a 90-day city-level report names a few hundred
    distinct places and would otherwise be a few hundred round trips (docs/PERFORMANCE.md).
    """
    wanted: set[str] = set()
    for row in rows:
        segments = row.get("segments", {})
        for key in ("geoTargetCity", "geoTargetRegion", "geoTargetCountry"):
            value = segments.get(key)
            if value:
                wanted.add(str(value))
    if not wanted:
        return {}
    quoted = ", ".join(f"'{name}'" for name in sorted(wanted))
    lookup = await client.search(
        customer_id,
        "SELECT geo_target_constant.resource_name, geo_target_constant.name, "
        "geo_target_constant.canonical_name "
        f"FROM geo_target_constant WHERE geo_target_constant.resource_name IN ({quoted})",
        context="geo_names",
    )
    out: dict[str, str] = {}
    for row in lookup:
        constant = row.get("geoTargetConstant", {})
        resource = constant.get("resourceName")
        if resource:
            out[resource] = constant.get("name") or constant.get("canonicalName") or resource
    return out


async def read_conversions(
    client: AdsClient, customer_id: str, window: Window, *, limit: int
) -> ReadResult:
    """What this account optimises toward, and what it actually recorded.

    The question behind it is "is the money being steered by something real": a campaign bidding
    to a conversion action that fires on every page view is spending against noise, and nothing
    in a performance report shows it.
    """
    rows = await client.search(
        customer_id,
        "SELECT "
        + _select(
            (
                "conversion_action.id",
                "conversion_action.name",
                "conversion_action.status",
                "conversion_action.type",
                "conversion_action.category",
                "conversion_action.origin",
                "conversion_action.primary_for_goal",
                "conversion_action.counting_type",
                "conversion_action.click_through_lookback_window_days",
                "conversion_action.view_through_lookback_window_days",
                "conversion_action.attribution_model_settings.attribution_model",
                "conversion_action.include_in_conversions_metric",
            ),
            ("metrics.all_conversions", "metrics.all_conversions_value"),
        )
        + f" FROM conversion_action WHERE {window.gaql()} LIMIT {limit + 1}",
        context="conversions",
    )
    shaped = []
    for row in rows:
        action = row.get("conversionAction", {})
        m = row.get("metrics", {})
        shaped.append(
            {
                "conversion_action_id": str(action.get("id") or ""),
                "name": action.get("name") or "",
                "status": action.get("status"),
                "type": action.get("type"),
                "category": action.get("category"),
                "origin": action.get("origin"),
                "primary_for_goal": bool(action.get("primaryForGoal", False)),
                "counting_type": action.get("countingType"),
                "click_through_lookback_days": action.get("clickThroughLookbackWindowDays"),
                "view_through_lookback_days": action.get("viewThroughLookbackWindowDays"),
                "attribution_model": (action.get("attributionModelSettings") or {}).get(
                    "attributionModel"
                ),
                "counts_toward_conversions": bool(
                    action.get("includeInConversionsMetric", False)
                ),
                "all_conversions": round(_float(m.get("allConversions")), 2),
                "all_conversions_value": round(_float(m.get("allConversionsValue")), 2),
            }
        )
    result = ReadResult(rows=shaped).truncated(limit)
    result.warnings.append("google_ads.warning.conversions_are_configuration")
    return result


#: How far ``change_event`` reaches back. Google's own limit, not ours — and asking for more
#: does not fail, it just quietly answers less, which is why the effective window is reported.
CHANGE_EVENT_DAYS = 30


async def read_changes(
    client: AdsClient, customer_id: str, window: Window, *, limit: int
) -> ReadResult:
    """What was actually changed, with old and new values.

    Two limits worth stating in the answer rather than in a docstring nobody reads at 2am:
    ``change_event`` reaches back **30 days** and no further, and **Google's own automatic
    adjustments do not appear in it at all** — a Smart Bidding change that tripled a CPA leaves
    no row here. An audit trail built on this alone would be confidently incomplete.
    """
    from datetime import timedelta

    warnings: list[str] = []
    earliest = datetime.now(UTC).date() - timedelta(days=CHANGE_EVENT_DAYS - 1)
    start = window.start
    if start < earliest:
        start = earliest
        warnings.append("google_ads.warning.changes_window_shortened")
    rows = await client.search(
        customer_id,
        "SELECT change_event.change_date_time, change_event.change_resource_type, "
        "change_event.resource_change_operation, change_event.changed_fields, "
        "change_event.change_resource_name, change_event.client_type, "
        "change_event.user_email, change_event.campaign, change_event.ad_group, "
        "change_event.old_resource, change_event.new_resource "
        "FROM change_event "
        f"WHERE change_event.change_date_time >= '{start.isoformat()}' "
        f"AND change_event.change_date_time <= '{window.end.isoformat()} 23:59:59' "
        f"ORDER BY change_event.change_date_time DESC LIMIT {limit + 1}",
        context="changes",
    )
    shaped = []
    for row in rows:
        event = row.get("changeEvent", {})
        shaped.append(
            {
                # In the **account's** timezone, unlike `fetched_at`, which is UTC. Two clocks
                # in one response, and the only honest fix is to say which is which.
                "changed_at": event.get("changeDateTime"),
                "resource_type": event.get("changeResourceType"),
                "operation": event.get("resourceChangeOperation"),
                "changed_resource": event.get("changeResourceName"),
                "campaign": event.get("campaign"),
                "ad_group": event.get("adGroup"),
                "changed_by": event.get("userEmail"),
                "client_type": event.get("clientType"),
                "changed_fields": _changed_fields(event),
            }
        )
    result = ReadResult(rows=shaped, warnings=warnings).truncated(limit)
    result.extra["effective_period"] = {"from": start.isoformat(), "to": window.end.isoformat()}
    result.extra["change_event_window_days"] = CHANGE_EVENT_DAYS
    result.warnings.append("google_ads.warning.changes_exclude_automation")
    return result


#: Values are stringified and cut here, not in the browser: a `new_resource` can carry a whole
#: ad with thirty assets, and a change list that ships them all is a payload nobody reads.
_CHANGE_VALUE_MAX = 300


def _changed_fields(event: dict[str, Any]) -> list[dict[str, Any]]:
    """Per changed field, its old and new value — which is the entire point of this read.

    ``changedFields`` is a FieldMask: a comma-separated list of paths into the old/new resource
    messages. Walking it is what turns "the campaign was updated" into "the daily budget went
    from 40 to 400".
    """
    mask = str(event.get("changedFields") or "")
    # `oldResource`/`newResource` are a **ChangedResource oneof**: a wrapper whose single
    # populated key names the resource type (`campaignBudget`, `adGroupCriterion`, …). The
    # FieldMask is relative to the message *inside* it, so walking from the wrapper finds
    # nothing and every change reads "from null to null" — true-looking and useless.
    old = _unwrap_changed_resource(event.get("oldResource"))
    new = _unwrap_changed_resource(event.get("newResource"))
    out: list[dict[str, Any]] = []
    for path in (p.strip() for p in mask.split(",") if p.strip()):
        out.append(
            {
                "field": path,
                "from": _at_path(old, path),
                "to": _at_path(new, path),
            }
        )
    return out


def _unwrap_changed_resource(payload: Any) -> dict[str, Any]:
    """The one populated message inside a ``ChangedResource``, or an empty dict."""
    if not isinstance(payload, dict):
        return {}
    for value in payload.values():
        if isinstance(value, dict):
            return value
    return {}


def _at_path(payload: dict[str, Any], path: str) -> str | None:
    """Follow a FieldMask path through a resource message, in Google's JSON casing."""
    node: Any = payload
    for part in path.split("."):
        camel = _camel(part)
        if not isinstance(node, dict):
            return None
        node = node.get(camel, node.get(part))
        if node is None:
            return None
    text = str(node)
    return text[:_CHANGE_VALUE_MAX] if len(text) > _CHANGE_VALUE_MAX else text


def _camel(snake: str) -> str:
    head, *rest = snake.split("_")
    return head + "".join(part.title() for part in rest)


async def read_recommendations(
    client: AdsClient, customer_id: str, *, limit: int
) -> ReadResult:
    """Google's own suggestions for this account.

    Worth exposing because it is the one read whose *content* is advice rather than data, and an
    assistant asked "what should we do about this account" would otherwise have to infer from
    metrics what Google already computed. Dismissed recommendations are excluded: somebody
    already decided.
    """
    rows = await client.search(
        customer_id,
        "SELECT recommendation.type, recommendation.resource_name, recommendation.campaign, "
        "recommendation.ad_group, recommendation.dismissed, "
        "recommendation.impact.base_metrics.impressions, "
        "recommendation.impact.base_metrics.clicks, "
        "recommendation.impact.base_metrics.cost_micros, "
        "recommendation.impact.base_metrics.conversions, "
        "recommendation.impact.potential_metrics.impressions, "
        "recommendation.impact.potential_metrics.clicks, "
        "recommendation.impact.potential_metrics.cost_micros, "
        "recommendation.impact.potential_metrics.conversions "
        "FROM recommendation "
        f"WHERE recommendation.dismissed = FALSE LIMIT {limit + 1}",
        context="recommendations",
    )
    shaped = []
    for row in rows:
        rec = row.get("recommendation", {})
        impact = rec.get("impact") or {}
        shaped.append(
            {
                "type": rec.get("type"),
                "resource_name": rec.get("resourceName"),
                "campaign": rec.get("campaign"),
                "ad_group": rec.get("adGroup"),
                "base": _impact(impact.get("baseMetrics")),
                "potential": _impact(impact.get("potentialMetrics")),
            }
        )
    return ReadResult(rows=shaped).truncated(limit)


def _impact(metrics: dict[str, Any] | None) -> dict[str, Any] | None:
    if not metrics:
        return None
    return {
        "impressions": round(_float(metrics.get("impressions")), 2),
        "clicks": round(_float(metrics.get("clicks")), 2),
        "cost": money(metrics.get("costMicros")),
        "conversions": round(_float(metrics.get("conversions")), 2),
    }


# --- the daily reads the nightly mirror stores ------------------------------------------------ #
#
# Same queries as the live reads above, plus `segments.date` — which changes them from "the
# period, folded" into "one row per day". Kept separate rather than adding a flag, because the
# *shape* differs: these return `(date, dim_key, label, metrics)` tuples destined for a table,
# and a caller that confused them with the folded reads would store a period total under a day.


def _daily_rows(
    rows: list[dict[str, Any]],
    *,
    key: Callable[[dict[str, Any]], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        day = str(row.get("segments", {}).get("date") or "")
        if not day:
            continue
        dim_key, label = key(row) if key else ("", "")
        out.append(
            {
                "date": date.fromisoformat(day),
                "dim_key": dim_key,
                "label": label,
                "metrics": metrics_block(row),
            }
        )
    return out


async def read_account_daily(
    client: AdsClient, customer_id: str, window: Window
) -> list[dict[str, Any]]:
    """One row per day for the whole account — the series every trend line is drawn from."""
    query = (
        "SELECT " + _select(("segments.date",), METRIC_FIELDS) + " FROM customer"
        f" WHERE {window.gaql()}"
    )
    rows = await client.search(customer_id, query, context="daily_account")
    return _daily_rows(rows)


async def read_campaign_daily(
    client: AdsClient, customer_id: str, window: Window
) -> list[dict[str, Any]]:
    """One row per campaign per day.

    Bounded by an agency's campaign count times the window, which is the largest of the three
    stored dimensions and still small — a client with forty campaigns is 280 rows a week.
    """
    query = (
        "SELECT "
        + _select(("segments.date", "campaign.id", "campaign.name"), METRIC_FIELDS)
        + " FROM campaign"
        + f" WHERE {window.gaql()}"
    )
    rows = await client.search(customer_id, query, context="daily_campaign")
    return _daily_rows(
        rows,
        key=lambda row: (
            str(row.get("campaign", {}).get("id") or ""),
            row.get("campaign", {}).get("name") or "",
        ),
    )


async def read_device_daily(
    client: AdsClient, customer_id: str, window: Window
) -> list[dict[str, Any]]:
    """One row per device per day, account-wide. Three rows a day, so effectively free."""
    query = (
        "SELECT " + _select(("segments.date", "segments.device"), METRIC_FIELDS) + " FROM customer"
        f" WHERE {window.gaql()}"
    )
    rows = await client.search(customer_id, query, context="daily_device")
    return _daily_rows(
        rows,
        key=lambda row: (
            str(row.get("segments", {}).get("device") or "UNKNOWN"),
            str(row.get("segments", {}).get("device") or "UNKNOWN"),
        ),
    )


async def read_account(client: AdsClient, customer_id: str, window: Window) -> dict[str, Any]:
    """The account's own totals and settings for the window — the top of a snapshot."""
    row = await client.search_one(
        customer_id,
        "SELECT "
        + _select(
            (
                "customer.id",
                "customer.descriptive_name",
                "customer.currency_code",
                "customer.time_zone",
                "customer.status",
                "customer.test_account",
                "customer.optimization_score",
                "customer.conversion_tracking_setting.conversion_tracking_status",
            ),
            METRIC_FIELDS,
        )
        + f" FROM customer WHERE {window.gaql()}",
        context="account",
    )
    if row is None:
        return {}
    customer = row.get("customer", {})
    tracking = customer.get("conversionTrackingSetting") or {}
    return {
        "customer_id": str(customer.get("id") or ""),
        "customer_name": customer.get("descriptiveName") or "",
        "currency": customer.get("currencyCode"),
        "account_timezone": customer.get("timeZone"),
        "account_status": customer.get("status"),
        "is_test_account": bool(customer.get("testAccount", False)),
        "optimization_score": _optional_float(customer.get("optimizationScore")),
        "conversion_tracking_status": tracking.get("conversionTrackingStatus"),
        "totals": metrics_block(row),
    }
