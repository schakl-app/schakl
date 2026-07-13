"""GA4 adapter — Analytics Admin API (list) + Analytics Data API (metrics/drill-downs).

Both are ordinary OAuth-bearer REST, so they ride the ``acting_as`` client directly. The
``analytics.readonly`` scope covers read on both. Metric names are GA4's own: note GA4 renamed
"conversions" to **keyEvents**, so we request ``keyEvents`` and expose it under both keys for
display continuity — asking for the retired ``conversions`` metric would 400 the whole report.
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING, Any

from app.modules.google.oauth import SCOPE_ANALYTICS
from app.modules.marketing.models import MarketingSource
from app.modules.marketing.sources.base import (
    AccountOption,
    DailyMetrics,
    DrilldownRow,
    DrilldownTable,
    register,
)

if TYPE_CHECKING:
    from authlib.integrations.httpx_client import AsyncOAuth2Client

ADMIN_API = "https://analyticsadmin.googleapis.com/v1beta"
DATA_API = "https://analyticsdata.googleapis.com/v1beta"

#: Requested from the Data API; ``conversions`` is derived from ``keyEvents`` (see module doc).
_REQUEST_METRICS = [
    "sessions", "totalUsers", "newUsers", "keyEvents", "engagementRate", "totalRevenue",
]


def _num(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _parse_ga4_date(value: str) -> date:
    return date(int(value[0:4]), int(value[4:6]), int(value[6:8]))


class GA4Adapter:
    source = MarketingSource.GA4.value
    scope = SCOPE_ANALYTICS
    drilldowns = ("top_pages", "channels", "devices")

    async def list_accounts(self, client: AsyncOAuth2Client) -> list[AccountOption]:
        options: list[AccountOption] = []
        page_token: str | None = None
        while True:
            params = {"pageSize": 200}
            if page_token:
                params["pageToken"] = page_token
            resp = await client.get(f"{ADMIN_API}/accountSummaries", params=params)
            resp.raise_for_status()
            body = resp.json()
            for account in body.get("accountSummaries", []):
                account_name = account.get("displayName") or account.get("account", "")
                for prop in account.get("propertySummaries", []):
                    external_id = prop.get("property", "")  # "properties/123456789"
                    if not external_id:
                        continue
                    options.append(
                        AccountOption(
                            external_id=external_id,
                            display_name=prop.get("displayName") or external_id,
                            config={"propertyType": prop.get("propertyType", "")},
                            account_hint=account_name,
                        )
                    )
            page_token = body.get("nextPageToken")
            if not page_token:
                break
        return options

    async def _run_report(
        self, client: AsyncOAuth2Client, external_id: str, body: dict
    ) -> dict:
        resp = await client.post(f"{DATA_API}/{external_id}:runReport", json=body)
        resp.raise_for_status()
        return resp.json()

    async def fetch_daily(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        start: date,
        end: date,
        config: dict,
    ) -> list[DailyMetrics]:
        date_range = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        # 1) headline metrics by day
        report = await self._run_report(
            client,
            external_id,
            {
                "dateRanges": [date_range],
                "dimensions": [{"name": "date"}],
                "metrics": [{"name": m} for m in _REQUEST_METRICS],
            },
        )
        currency = report.get("metadata", {}).get("currencyCode") or config.get("currency")
        by_day: dict[date, dict[str, float]] = {}
        for row in report.get("rows", []):
            day = _parse_ga4_date(row["dimensionValues"][0]["value"])
            values = [v.get("value") for v in row.get("metricValues", [])]
            metrics = {name: _num(values[i]) for i, name in enumerate(_REQUEST_METRICS)}
            metrics["conversions"] = metrics.get("keyEvents", 0.0)  # display alias
            metrics["channels"] = {}
            by_day[day] = metrics

        # 2) acquisition split (sessions by default channel group), merged per day
        channels = await self._run_report(
            client,
            external_id,
            {
                "dateRanges": [date_range],
                "dimensions": [{"name": "date"}, {"name": "sessionDefaultChannelGroup"}],
                "metrics": [{"name": "sessions"}],
            },
        )
        for row in channels.get("rows", []):
            day = _parse_ga4_date(row["dimensionValues"][0]["value"])
            group = row["dimensionValues"][1]["value"] or "Other"
            sessions = _num(row["metricValues"][0]["value"])
            by_day.setdefault(day, {"channels": {}})
            by_day[day].setdefault("channels", {})[group] = sessions

        return [
            DailyMetrics(day=day, metrics=metrics, currency=currency)
            for day, metrics in sorted(by_day.items())
        ]

    async def drilldown(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        kind: str,
        start: date,
        end: date,
        config: dict,
    ) -> DrilldownTable:
        date_range = {"startDate": start.isoformat(), "endDate": end.isoformat()}
        spec = {
            "top_pages": (["screenPageViews", "sessions"], "unifiedScreenName"),
            "channels": (["sessions", "keyEvents"], "sessionDefaultChannelGroup"),
            "devices": (["sessions"], "deviceCategory"),
        }.get(kind, (["sessions"], "sessionDefaultChannelGroup"))
        metrics, dimension = spec
        if kind == "top_pages":
            dimension = "pagePath"
        report = await self._run_report(
            client,
            external_id,
            {
                "dateRanges": [date_range],
                "dimensions": [{"name": dimension}],
                "metrics": [{"name": m} for m in metrics],
                "limit": 10,
                "orderBys": [{"metric": {"metricName": metrics[0]}, "desc": True}],
            },
        )
        rows = [
            DrilldownRow(
                label=row["dimensionValues"][0]["value"],
                metrics={
                    m: _num(row["metricValues"][i]["value"]) for i, m in enumerate(metrics)
                },
            )
            for row in report.get("rows", [])
        ]
        return DrilldownTable(kind=kind, columns=metrics, rows=rows)

    def deep_link(self, external_id: str, config: dict) -> str:
        numeric = external_id.split("/")[-1]
        return f"https://analytics.google.com/analytics/web/#/p{numeric}/reports/intelligenthome"


register(GA4Adapter())
