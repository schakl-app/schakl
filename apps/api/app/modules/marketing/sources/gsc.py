"""Search Console adapter — Search Console API v3 (``webmasters.readonly``).

Ordinary OAuth-bearer REST on ``www.googleapis.com/webmasters/v3``. GSC data **finalizes ~2-3
days late**, so the service re-pulls a trailing window on every run and upserts — late data
self-heals. The ``movers`` drill-down answers the marketeer's "which keywords/pages moved?"
directly: it diffs average position between the range and the equal-length window before it.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from app.modules.google.oauth import SCOPE_SEARCH_CONSOLE
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

API = "https://www.googleapis.com/webmasters/v3"


def _num(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


class GSCAdapter:
    source = MarketingSource.GSC.value
    scope = SCOPE_SEARCH_CONSOLE
    drilldowns = ("top_queries", "top_pages", "movers")

    async def list_accounts(self, client: AsyncOAuth2Client) -> list[AccountOption]:
        resp = await client.get(f"{API}/sites")
        resp.raise_for_status()
        options: list[AccountOption] = []
        for entry in resp.json().get("siteEntry", []):
            site_url = entry.get("siteUrl", "")
            if not site_url:
                continue
            is_domain = site_url.startswith("sc-domain:")
            display = site_url[len("sc-domain:") :] if is_domain else site_url
            options.append(
                AccountOption(
                    external_id=site_url,
                    display_name=display,
                    config={
                        "siteType": "domain" if is_domain else "url_prefix",
                        "permissionLevel": entry.get("permissionLevel", ""),
                    },
                )
            )
        return options

    async def _query(self, client: AsyncOAuth2Client, external_id: str, body: dict) -> list[dict]:
        encoded = quote(external_id, safe="")
        resp = await client.post(f"{API}/sites/{encoded}/searchAnalytics/query", json=body)
        resp.raise_for_status()
        return resp.json().get("rows", [])

    async def fetch_daily(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        start: date,
        end: date,
        config: dict,
    ) -> list[DailyMetrics]:
        rows = await self._query(
            client,
            external_id,
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": ["date"],
                "rowLimit": 1000,
            },
        )
        out: list[DailyMetrics] = []
        for row in rows:
            day = date.fromisoformat(row["keys"][0])
            out.append(
                DailyMetrics(
                    day=day,
                    metrics={
                        "clicks": _num(row.get("clicks")),
                        "impressions": _num(row.get("impressions")),
                        "ctr": _num(row.get("ctr")),
                        "position": _num(row.get("position")),
                    },
                )
            )
        return out

    async def drilldown(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        kind: str,
        start: date,
        end: date,
        config: dict,
    ) -> DrilldownTable:
        if kind == "movers":
            return await self._movers(client, external_id, start, end)
        dimension = "page" if kind == "top_pages" else "query"
        rows = await self._query(
            client,
            external_id,
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": [dimension],
                "rowLimit": 10,
            },
        )
        columns = ["clicks", "impressions", "ctr", "position"]
        out = [
            DrilldownRow(
                label=row["keys"][0],
                metrics={c: _num(row.get(c)) for c in columns},
                href=row["keys"][0] if dimension == "page" else None,
            )
            for row in rows
        ]
        return DrilldownTable(kind=kind, columns=columns, rows=out)

    async def _movers(
        self, client: AsyncOAuth2Client, external_id: str, start: date, end: date
    ) -> DrilldownTable:
        """Queries whose average position moved most vs the equal window before the range."""
        span = (end - start).days + 1
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - timedelta(days=span - 1)

        async def positions(a: date, b: date) -> dict[str, dict[str, float]]:
            rows = await self._query(
                client,
                external_id,
                {
                    "startDate": a.isoformat(),
                    "endDate": b.isoformat(),
                    "dimensions": ["query"],
                    "rowLimit": 250,
                },
            )
            return {
                r["keys"][0]: {"position": _num(r.get("position")), "clicks": _num(r.get("clicks"))}
                for r in rows
            }

        now, before = await positions(start, end), await positions(prev_start, prev_end)
        movers: list[DrilldownRow] = []
        for query, cur in now.items():
            prev = before.get(query)
            if prev is None:
                continue
            # A *drop* in the position number is an improvement; store the signed change.
            change = round(prev["position"] - cur["position"], 1)
            movers.append(
                DrilldownRow(
                    label=query,
                    metrics={
                        "position": cur["position"],
                        "position_change": change,
                        "clicks": cur["clicks"],
                    },
                )
            )
        movers.sort(key=lambda r: abs(r.metrics["position_change"]), reverse=True)
        return DrilldownTable(
            kind="movers",
            columns=["position", "position_change", "clicks"],
            rows=movers[:10],
        )

    def deep_link(self, external_id: str, config: dict) -> str:
        return f"https://search.google.com/search-console?resource_id={quote(external_id, safe='')}"


register(GSCAdapter())
