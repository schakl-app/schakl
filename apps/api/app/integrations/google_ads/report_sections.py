"""What ``google_ads`` contributes to a periodic client report. Business-licensed — see LICENSE.

The panels pattern applied to documents (#300): the reporting module composes whatever sections
the enabled modules declare and names none of them. Adding "wat de advertenties deden" to every
client's monthly report is therefore a change here, where the Ads data lives.

**Both sections read the nightly mirror, not Google.** That is not an optimisation, it is what
makes a report of last March still printable next March: a live read of a closed period costs
API quota to return figures that cannot have changed, and a report regenerated a year later
would ask Google for a window it no longer serves at that granularity. The one thing the mirror
cannot answer — which search terms wasted money *this* period — is deliberately not a section:
it belongs in the marketeer's working screen, where acting on it is one click away.

**Performance is for both audiences; the change history is internal only.** The split is not
"client gets less of everything" — an internal analysis that could not see what the advertising
did would be blind to most of what the marketeer needs, which is the mistake ``marketing``
already records having made. What is withheld is exactly one thing, and for a reason: "the daily
budget went from 40 to 400 on the 3rd, by stan@" is the sentence an agency wants in front of
itself and precisely the one it does not want in front of the client whose budget that was.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.periods import ComparePeriod
from app.core.tenancy import RequestContext
from app.integrations.google_ads.models import GoogleAdsAccount, GoogleAdsChange
from app.integrations.google_ads.trends import read_trend
from app.registry import AUDIENCE_BOTH, AUDIENCE_INTERNAL, ReportSectionSpec, ReportWindow

#: The metrics a client's report prints, in reading order. Deliberately short: a report is read
#: once by somebody who is not a marketeer, and eleven columns is a table nobody finishes.
_CLIENT_COLUMNS = ("cost", "clicks", "impressions", "conversions", "cost_per_conversion")

#: How many campaigns the client section names. Beyond this the tail is noise on a printed page,
#: and the total already accounts for it — so nothing is hidden, only unlisted.
_MAX_CAMPAIGNS = 8

#: How many changes the internal section lists. It is a summary, not the audit trail; the full
#: history lives on the account's own screen.
_MAX_CHANGES = 25

_READ = "google_ads.account.read"


async def _accounts(ctx: RequestContext, company_id: uuid.UUID) -> list[GoogleAdsAccount]:
    stmt = (
        ctx.repo(GoogleAdsAccount)
        .scoped_select()
        .where(
            GoogleAdsAccount.company_id == company_id,
            GoogleAdsAccount.active.is_(True),
        )
    )
    return list((await ctx.session.scalars(stmt)).all())


async def _performance(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    """What the advertising did this period, against the same period a year earlier.

    Returns ``None`` — not an empty table — when the client runs no ads. A client with no Ads
    account simply has no advertising section, which is not an error and must not print a row of
    zeros that reads as "we spent nothing and got nothing".
    """
    accounts = await _accounts(ctx, window.company_id)
    if not accounts:
        return None

    rows: list[dict[str, Any]] = []
    totals: dict[str, float] = {}
    compare: dict[str, float] = {}
    currency: str | None = None
    notes: list[dict[str, str]] = []
    series: list[dict[str, Any]] = []

    for account in accounts:
        trend = await read_trend(
            ctx,
            account.id,
            start=window.start,
            end=window.end,
            # The reporting module already decided the comparison for this client and handed us
            # its dates; passing YEAR here and then *using the window's own* compare dates would
            # be two opinions about one question. `read_trend` derives them, and the report's
            # own header states what it compared — #312's rule, one layer along.
            mode=ComparePeriod.YEAR,
            breakdown_limit=_MAX_CAMPAIGNS,
        )
        currency = currency or trend.currency or account.currency_code
        if trend.missing_days:
            # A note, never a printed caveat: the run's warnings are for the agency, and a
            # client's document does not explain our sync schedule to them.
            notes.append(
                {
                    "code": "google_ads.days_not_synced",
                    "detail": f"{account.descriptive_name}: {trend.missing_days}",
                }
            )
        for key in _CLIENT_COLUMNS:
            value = trend.totals.get(key)
            if isinstance(value, int | float):
                totals[key] = round(totals.get(key, 0.0) + value, 2)
            previous = trend.previous_totals.get(key)
            if isinstance(previous, int | float):
                compare[key] = round(compare.get(key, 0.0) + previous, 2)
        rows.extend(
            {
                "label": item.get("campaign_name") or item.get("campaign_id"),
                "account": account.descriptive_name,
                **{key: item.get(key) for key in _CLIENT_COLUMNS},
            }
            for item in trend.breakdown
        )
        series.extend(
            {"date": point.day.isoformat(), "cost": point.metrics.get("cost", 0)}
            for point in trend.series
        )

    if not rows and not totals:
        # Linked but nothing stored yet — a first sync that has not run. Better no section than
        # a table of zeros a client would read as a month of doing nothing.
        return None

    rows.sort(key=lambda row: float(row.get("cost") or 0), reverse=True)
    # Ratios are re-derived from the summed components rather than added: summing two accounts'
    # cost-per-conversion produces a number that is not any account's CPA and not the client's.
    for bucket in (totals, compare):
        conversions = bucket.get("conversions") or 0
        bucket["cost_per_conversion"] = (
            round(bucket.get("cost", 0.0) / conversions, 2) if conversions else None
        )
    return {
        "kind": "table",
        "columns": list(_CLIENT_COLUMNS),
        "rows": rows[:_MAX_CAMPAIGNS],
        "totals": totals,
        "compare": compare or None,
        "currency": currency,
        "chart": {"type": "line", "series": series, "value_key": "cost"} if series else None,
        "notes": notes,
    }


async def _changes(ctx: RequestContext, window: ReportWindow) -> dict[str, Any] | None:
    """What was changed in the account this period, and by whom — **internal only**.

    Reads the mirror rather than Google, which is the point of mirroring: `change_event` is a
    30-day window, and a report generated three weeks late for last month would otherwise find
    half of it already gone.

    Internal audience because of what it says: "the daily budget went from 40 to 400 on the 3rd,
    by stan@" is exactly the sentence an agency wants in front of itself and exactly the one it
    does not want in front of the client whose budget that was.
    """
    accounts = await _accounts(ctx, window.company_id)
    if not accounts:
        return None
    ids = [account.id for account in accounts]
    names = {account.id: account.descriptive_name for account in accounts}
    stmt = (
        ctx.repo(GoogleAdsChange)
        .scoped_select()
        .where(
            GoogleAdsChange.account_id.in_(ids),
            GoogleAdsChange.changed_at >= _start_of(window.start),
            GoogleAdsChange.changed_at < _start_of(window.end, inclusive_end=True),
        )
        .order_by(GoogleAdsChange.changed_at.desc())
        .limit(_MAX_CHANGES + 1)
    )
    changes = list((await ctx.session.scalars(stmt)).all())
    if not changes:
        return None
    notes: list[dict[str, str]] = [
        # Stated on every run, because the tempting mistake is to read a table with four hundred
        # days in it as authoritative.
        {"code": "google_ads.changes_exclude_automation", "detail": ""}
    ]
    if len(changes) > _MAX_CHANGES:
        notes.append({"code": "google_ads.changes_truncated", "detail": str(_MAX_CHANGES)})
        changes = changes[:_MAX_CHANGES]
    return {
        "kind": "list",
        "columns": ["changed_at", "resource_type", "operation", "changed_by"],
        "rows": [
            {
                "changed_at": change.changed_at.isoformat(),
                "account": names.get(change.account_id, ""),
                "resource_type": change.resource_type,
                "operation": change.operation,
                "changed_by": change.changed_by,
                "changed_fields": change.changed_fields,
            }
            for change in changes
        ],
        "totals": {},
        "compare": None,
        "chart": None,
        "notes": notes,
    }


def _start_of(day, *, inclusive_end: bool = False):
    """A date bound as an instant. The stored ``changed_at`` is timezone-aware.

    The window's dates come from the report and are the *client's* calendar days; the stored
    instants were resolved from each account's own zone. Comparing a date to an aware column
    would be a type error, and comparing it in UTC would clip an evening's changes for any
    account east of Greenwich — so the bound is deliberately generous by one day at the end.
    """
    from datetime import UTC, datetime, time, timedelta

    anchor = day + timedelta(days=1) if inclusive_end else day
    return datetime.combine(anchor, time.min, tzinfo=UTC)


GOOGLE_ADS_REPORT_SECTIONS: list[ReportSectionSpec] = [
    ReportSectionSpec(
        key="google_ads.performance",
        title_key="google_ads.report.performance.title",
        provider=_performance,
        brief_key="google_ads.report.performance.brief",
        source_key="reporting.source.gads",
        # Both: the client reads what their advertising did, and the marketeer's own analysis
        # would be worth little without it. Only the change history below is withheld.
        audience=AUDIENCE_BOTH,
        requires_permission=_READ,
        # After marketing's traffic and rankings (10-40) and before its conversions (70): a
        # client reads "where the visitors came from" before "what we paid for some of them".
        position=45,
    ),
    ReportSectionSpec(
        key="google_ads.changes",
        title_key="google_ads.report.changes.title",
        provider=_changes,
        brief_key="google_ads.report.changes.brief",
        source_key="reporting.source.gads",
        audience=AUDIENCE_INTERNAL,
        requires_permission=_READ,
        position=95,
    ),
]
