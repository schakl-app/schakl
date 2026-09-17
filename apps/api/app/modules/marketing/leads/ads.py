"""The Google Ads half of a leads dashboard: three GAQL reads and the widgets over them.

Kept apart from the GA4 half on purpose, and never joined to it on a date: costs and events
carry different dimensions, and a join on the one column they share multiplies rows the moment
a second dimension appears (the Looker lesson). Every Ads widget reads one of three queries:

- **daily** (``FROM customer`` by ``segments.date``) — the scorecards and the cost/conversions
  trend;
- **campaigns** (``FROM campaign``) — the campaign table and the impression-share table, which
  GAQL lets travel in one query because both are campaign-level metrics;
- **actions** (``FROM campaign`` segmented by ``conversion_action_name``) — conversions per
  action per campaign, which the Looker connector could not show and the API can. It cannot
  carry ``cost_micros`` or ``clicks`` beside the segment, so it is a query of its own.

Two integrity rules from ``docs/GOOGLE_ADS.md``: ``ctr``, ``conversion_rate`` and impression
share are fractions (multiplied at display), and a ratio the API did not send is ``None``, never
``0`` — a zero reads as "measured zero", which is a different claim from "not computable".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.modules.marketing.leads.profile import DIM_SERVICE, LeadProfile
from app.modules.marketing.leads.schemas import (
    LeadColumn,
    LeadRow,
    LeadSeries,
    LeadUnavailable,
    LeadWidget,
)
from app.modules.marketing.leads.widgets import WIDGETS, WidgetDef, missing_for

Q_DAILY = "ads_daily"
Q_CAMPAIGNS = "ads_campaigns"
Q_ACTIONS = "ads_actions"


def queries(start: date, end: date) -> dict[str, str]:
    span = f"segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
    return {
        Q_DAILY: (
            "SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.impressions, "
            "metrics.conversions, metrics.conversions_value, metrics.all_conversions, "
            "metrics.all_conversions_value FROM customer "
            f"WHERE {span}"
        ),
        Q_CAMPAIGNS: (
            "SELECT campaign.id, campaign.name, campaign.status, "
            "campaign.advertising_channel_type, "
            "metrics.cost_micros, metrics.clicks, metrics.impressions, metrics.ctr, "
            "metrics.conversions, metrics.conversions_from_interactions_rate, "
            "metrics.cost_per_conversion, metrics.conversions_value, metrics.all_conversions, "
            "metrics.all_conversions_value, metrics.search_impression_share, "
            "metrics.search_rank_lost_impression_share, "
            "metrics.search_budget_lost_impression_share FROM campaign "
            f"WHERE {span} ORDER BY metrics.cost_micros DESC"
        ),
        Q_ACTIONS: (
            "SELECT campaign.name, segments.conversion_action_name, "
            "segments.conversion_action_category, metrics.conversions, metrics.all_conversions, "
            "metrics.conversions_value, metrics.all_conversions_value FROM campaign "
            f"WHERE {span} AND metrics.all_conversions > 0 "
            "ORDER BY metrics.all_conversions DESC"
        ),
    }


def _num(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _opt(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _money(raw: Any) -> float:
    return round(_num(raw) / 1_000_000, 2)


@dataclass
class AdsData:
    """The three answers, in the shape the widgets read."""

    daily: list[dict[str, Any]] = field(default_factory=list)
    campaigns: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    currency: str | None = None
    #: ``{query key: reason}`` for the queries that failed — each costs its own widgets only.
    failed: dict[str, str] = field(default_factory=dict)

    def available(self, key: str) -> bool:
        return key not in self.failed


def compute_ads(
    profile: LeadProfile,
    data: AdsData,
    *,
    start: date,
    end: date,
    locale: str,
) -> tuple[list[LeadWidget], list[LeadUnavailable]]:
    widgets: list[LeadWidget] = []
    unavailable: list[LeadUnavailable] = []
    hidden = set(profile.hidden_widgets)
    for definition in WIDGETS:
        if definition.source != "gads":
            continue
        reason = missing_for(definition, profile, has_ads=True)
        if reason:
            unavailable.append(LeadUnavailable(key=definition.key, reason=reason))
            continue
        if definition.key in hidden:
            unavailable.append(LeadUnavailable(key=definition.key, reason="hidden"))
            continue
        query = definition.reports[0]
        if not data.available(query):
            unavailable.append(LeadUnavailable(key=definition.key, reason=f"report_failed:{query}"))
            continue
        widget = _ADS_BUILDERS[definition.key](profile, data, definition, start, end, locale)
        if widget is not None:
            widgets.append(widget)
    return widgets, unavailable


def _base(definition: WidgetDef, currency: str | None, **kwargs) -> LeadWidget:  # noqa: ANN003
    return LeadWidget(
        key=definition.key,
        part="ads",
        kind=definition.kind,  # type: ignore[arg-type]
        source="gads",
        title_key=f"marketing.leads.widget.{definition.key}",
        reports=list(definition.reports),
        currency=currency,
        **kwargs,
    )


def _totals(data: AdsData) -> dict[str, float]:
    out = {
        "cost": 0.0,
        "clicks": 0.0,
        "impressions": 0.0,
        "conversions": 0.0,
        "conversions_value": 0.0,
        "all_conversions": 0.0,
        "all_conversions_value": 0.0,
    }
    for row in data.daily:
        m = row.get("metrics", {})
        out["cost"] += _money(m.get("costMicros"))
        out["clicks"] += _num(m.get("clicks"))
        out["impressions"] += _num(m.get("impressions"))
        out["conversions"] += _num(m.get("conversions"))
        out["conversions_value"] += _num(m.get("conversionsValue"))
        out["all_conversions"] += _num(m.get("allConversions"))
        out["all_conversions_value"] += _num(m.get("allConversionsValue"))
    out["cost"] = round(out["cost"], 2)
    return out


def _cost(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    return _base(d, data.currency, value=_totals(data)["cost"], unit="money")


def _conversions(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    t = _totals(data)
    return _base(
        d,
        data.currency,
        value=t["conversions"],
        secondary=t["all_conversions"],
        secondary_key="marketing.leads.all_conversions",
        note_key="marketing.leads.note.primary_conversions",
    )


def _cost_per_conversion(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    t = _totals(data)
    value = round(t["cost"] / t["conversions"], 2) if t["conversions"] > 0 else None
    return _base(d, data.currency, value=value, unit="money")


def _conversion_value(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    t = _totals(data)
    return _base(
        d,
        data.currency,
        value=t["conversions_value"],
        secondary=t["all_conversions_value"],
        secondary_key="marketing.leads.all_conversions",
        unit="money",
        note_key="marketing.leads.note.conversion_value",
    )


def _by_day(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    index = {day.isoformat(): i for i, day in enumerate(days)}
    cost = [0.0] * len(days)
    conversions = [0.0] * len(days)
    for row in data.daily:
        i = index.get(str(row.get("segments", {}).get("date", "")))
        if i is None:
            continue
        m = row.get("metrics", {})
        cost[i] += _money(m.get("costMicros"))
        conversions[i] += _num(m.get("conversions"))
    return _base(
        d,
        data.currency,
        series=LeadSeries(
            dates=days,
            values={"cost": [round(c, 2) for c in cost], "conversions": conversions},
            bars=["cost"],
            units={"cost": "money", "conversions": "count"},
        ),
    )


def _campaigns(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    rows: list[LeadRow] = []
    for row in data.campaigns:
        m = row.get("metrics", {})
        c = row.get("campaign", {})
        cost = _money(m.get("costMicros"))
        conv = _num(m.get("conversions"))
        rows.append(
            LeadRow(
                key=str(c.get("id") or c.get("name") or ""),
                label=c.get("name", ""),
                values={
                    "cost": cost,
                    "clicks": _num(m.get("clicks")),
                    "impressions": _num(m.get("impressions")),
                    "ctr": _opt(m.get("ctr")),
                    "conversions": conv,
                    "conversion_rate": _opt(m.get("conversionsFromInteractionsRate")),
                    "cost_per_conversion": round(cost / conv, 2) if conv > 0 else None,
                    "conversions_value": _num(m.get("conversionsValue")),
                    "all_conversions": _num(m.get("allConversions")),
                },
                texts={
                    "status": str(c.get("status") or ""),
                    "channel_type": str(c.get("advertisingChannelType") or ""),
                },
            )
        )
    return _base(
        d,
        data.currency,
        columns=[
            LeadColumn(key="cost", title_key="marketing.leads.col.cost", unit="money"),
            LeadColumn(key="clicks", title_key="marketing.leads.col.clicks"),
            LeadColumn(key="ctr", title_key="marketing.leads.col.ctr", unit="ratio"),
            LeadColumn(key="conversions", title_key="marketing.leads.col.conversions"),
            LeadColumn(
                key="conversion_rate", title_key="marketing.leads.col.conversion_rate", unit="ratio"
            ),
            LeadColumn(
                key="cost_per_conversion",
                title_key="marketing.leads.col.cost_per_conversion",
                unit="money",
            ),
            LeadColumn(
                key="conversions_value",
                title_key="marketing.leads.col.conversions_value",
                unit="money",
            ),
        ],
        rows=rows,
        note_key="marketing.leads.note.primary_conversions",
    )


def _impression_share(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    rows: list[LeadRow] = []
    for row in data.campaigns:
        m = row.get("metrics", {})
        c = row.get("campaign", {})
        share = _opt(m.get("searchImpressionShare"))
        rank = _opt(m.get("searchRankLostImpressionShare"))
        budget = _opt(m.get("searchBudgetLostImpressionShare"))
        if share is None and rank is None and budget is None:
            continue
        rows.append(
            LeadRow(
                key=str(c.get("id") or c.get("name") or ""),
                label=c.get("name", ""),
                values={"impression_share": share, "rank_lost": rank, "budget_lost": budget},
            )
        )
    return _base(
        d,
        data.currency,
        columns=[
            LeadColumn(
                key="impression_share",
                title_key="marketing.leads.col.impression_share",
                unit="ratio",
            ),
            LeadColumn(key="rank_lost", title_key="marketing.leads.col.rank_lost", unit="ratio"),
            LeadColumn(
                key="budget_lost", title_key="marketing.leads.col.budget_lost", unit="ratio"
            ),
        ],
        rows=rows,
        note_key="marketing.leads.note.impression_share",
    )


def _conversion_actions(profile, data, d, start, end, locale):  # noqa: ANN001, ARG001
    service_spec = profile.dimension(DIM_SERVICE)
    rows: list[LeadRow] = []
    total = 0.0
    for row in data.actions:
        m = row.get("metrics", {})
        s = row.get("segments", {})
        c = row.get("campaign", {})
        action = str(s.get("conversionActionName") or "")
        campaign = str(c.get("name") or "")
        service_raw = profile.ads.action_services.get(action, "")
        service = (
            service_spec.value_label(service_raw, locale)
            if service_spec and service_raw
            else service_raw
        )
        all_conv = _num(m.get("allConversions"))
        total += all_conv
        rows.append(
            LeadRow(
                key=f"{campaign}|{action}",
                label=action,
                values={
                    "conversions": _num(m.get("conversions")),
                    "all_conversions": all_conv,
                    "conversions_value": _num(m.get("conversionsValue")),
                },
                texts={
                    "campaign": campaign,
                    "category": str(s.get("conversionActionCategory") or ""),
                    "service": service,
                },
            )
        )
    return _base(
        d,
        data.currency,
        columns=[
            LeadColumn(key="campaign", title_key="marketing.leads.col.campaign", unit="text"),
            LeadColumn(key="service", title_key="marketing.leads.col.service", unit="text"),
            LeadColumn(key="conversions", title_key="marketing.leads.col.conversions"),
            LeadColumn(key="all_conversions", title_key="marketing.leads.col.all_conversions"),
            LeadColumn(
                key="conversions_value",
                title_key="marketing.leads.col.conversions_value",
                unit="money",
            ),
        ],
        rows=rows,
        total=total,
    )


_ADS_BUILDERS = {
    "ads_cost": _cost,
    "ads_conversions": _conversions,
    "ads_cost_per_conversion": _cost_per_conversion,
    "ads_conversion_value": _conversion_value,
    "ads_by_day": _by_day,
    "ads_campaigns": _campaigns,
    "ads_impression_share": _impression_share,
    "ads_conversion_actions": _conversion_actions,
}
