"""Trends from stored rows. **No Google call happens here at all.**

Business-licensed — see LICENSE.

That is the whole reason :class:`~app.integrations.google_ads.models.GoogleAdsMetricDaily` exists. A
tile showing this month against the same month last year is otherwise two live Ads calls per
client per page load, against a shared daily operation quota, for numbers that stopped changing
weeks ago — and the second of those calls is for a period whose figures are *final*, so it can
only ever return what it returned yesterday.

Two rules from #312 are load-bearing and easy to lose:

* **A percentage is a claim about two spans, so both are in the payload.** "up 21 %" over an
  unnamed period is a sentence that can be printed over any two dates at all, which is why a
  comparison set to the wrong thing looks exactly like one set to the right thing. The compared
  window's dates come back with the numbers.
* **Two bounded windows, never their hull.** Reading `BETWEEN` the earliest and latest date of a
  year-over-year comparison drags eleven unread months through the session. The predicate is an
  `OR` of two ranges.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import and_, or_

from app.core.periods import ComparePeriod, compare_window
from app.integrations.google_ads.models import GoogleAdsDimension, GoogleAdsMetricDaily
from app.integrations.google_ads.reporting import totals_from_rows


@dataclass(frozen=True)
class TrendSeries:
    """One day of the stored series, for a chart."""

    day: date
    metrics: dict[str, Any]


@dataclass
class TrendResult:
    current_start: date
    current_end: date
    compare_start: date
    compare_end: date
    compare_mode: str
    totals: dict[str, Any] = field(default_factory=dict)
    previous_totals: dict[str, Any] = field(default_factory=dict)
    change: dict[str, Any] = field(default_factory=dict)
    series: list[TrendSeries] = field(default_factory=list)
    breakdown: list[dict[str, Any]] = field(default_factory=list)
    #: Days in the current window that have no stored row. Reported rather than smoothed over: a
    #: chart with a silent gap reads as a day with no spend, which is a different claim from a
    #: day nobody has synced yet.
    missing_days: int = 0
    currency: str | None = None


def delta(now: Any, then: Any) -> dict[str, Any] | None:
    """Absolute and relative change, or ``None`` when there is nothing to compare against.

    A percentage against a zero baseline is undefined, not infinite growth — and anything that
    renders `inf` will eventually put it in a sentence in front of a client.
    """
    if not isinstance(now, int | float) or not isinstance(then, int | float):
        return None
    absolute = round(now - then, 4)
    return {
        "from": then,
        "to": now,
        "absolute": absolute,
        "relative": round(absolute / then, 4) if then else None,
    }


async def read_trend(
    ctx: Any,
    account_id: uuid.UUID,
    *,
    start: date,
    end: date,
    mode: ComparePeriod,
    breakdown_limit: int = 10,
) -> TrendResult:
    """The stored series for a window, its comparison, and the deltas between them."""
    compare_start, compare_end = compare_window(start, end, mode)
    repo = ctx.repo(GoogleAdsMetricDaily)

    # One statement for both windows, as an OR of two bounded ranges. `scoped_select` carries the
    # tenant and the company horizon; the horizon reaches this table through its account, which
    # is why the model declares a clause rather than relying on a `company_id` it does not have.
    stmt = repo.scoped_select().where(
        GoogleAdsMetricDaily.account_id == account_id,
        or_(
            and_(
                GoogleAdsMetricDaily.date >= start,
                GoogleAdsMetricDaily.date <= end,
            ),
            and_(
                GoogleAdsMetricDaily.date >= compare_start,
                GoogleAdsMetricDaily.date <= compare_end,
            ),
        ),
        GoogleAdsMetricDaily.dimension.in_(
            (GoogleAdsDimension.ACCOUNT.value, GoogleAdsDimension.CAMPAIGN.value)
        ),
    )
    rows = list((await ctx.session.scalars(stmt)).all())

    account_rows = [r for r in rows if r.dimension == GoogleAdsDimension.ACCOUNT.value]
    current = [r for r in account_rows if start <= r.date <= end]
    previous = [r for r in account_rows if compare_start <= r.date <= compare_end]

    totals = totals_from_rows([r.metrics for r in current])
    previous_totals = totals_from_rows([r.metrics for r in previous])

    campaign_rows = [
        r
        for r in rows
        if r.dimension == GoogleAdsDimension.CAMPAIGN.value and start <= r.date <= end
    ]
    by_campaign: dict[str, list] = {}
    labels: dict[str, str] = {}
    for row in campaign_rows:
        by_campaign.setdefault(row.dim_key, []).append(row.metrics)
        # The most recent label wins: a campaign renamed mid-window reads under the name it has
        # now, while the historical rows keep the name they were stored with.
        labels[row.dim_key] = row.label or labels.get(row.dim_key, row.dim_key)
    breakdown = [
        {"campaign_id": key, "campaign_name": labels.get(key, key), **totals_from_rows(metrics)}
        for key, metrics in by_campaign.items()
    ]
    breakdown.sort(key=lambda item: item["cost"], reverse=True)

    return TrendResult(
        current_start=start,
        current_end=end,
        compare_start=compare_start,
        compare_end=compare_end,
        compare_mode=mode.value,
        totals=totals,
        previous_totals=previous_totals,
        change={key: delta(totals.get(key), previous_totals.get(key)) for key in totals},
        series=[TrendSeries(day=r.date, metrics=r.metrics) for r in sorted(current, key=_day)],
        breakdown=breakdown[:breakdown_limit],
        missing_days=max(0, (end - start).days + 1 - len(current)),
        currency=next((r.currency for r in current if r.currency), None),
    )


def _day(row: GoogleAdsMetricDaily) -> date:
    return row.date
