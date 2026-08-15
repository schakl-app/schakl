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
    AUTH_GOOGLE,
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
    auth = AUTH_GOOGLE
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

    async def keyword_rows(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        start: date,
        end: date,
        compare_start: date | None = None,
        compare_end: date | None = None,
        *,
        limit: int = 25,
        min_impressions: float = 10.0,
        max_position: float = 25.0,
    ) -> list[dict[str, Any]]:
        """Per-query positions for the period — the rankings section, from Search Console.

        Until this existed, ``marketing.rankings`` was produced from **SE Ranking and nothing
        else**, so a client without that subscription got no keyword section at all — silently,
        with nothing on the document or the review screen saying one had been withheld. Search
        Console is connected for practically every client and answers the question directly; the
        adapter already had ``top_queries`` and ``movers`` and the report used neither.

        It answers the same shape ``SERankingAdapter.keyword_rows`` does — ``keyword``,
        ``group``, ``begin``, ``end``, ``change``, ``status`` — so the section, the renderer and
        the model need to know nothing about where a ranking came from. Three fields differ
        honestly rather than being invented:

        * **no ``landing_page``**. A query dimension knows what was searched, not which page
          answered it; asking for both dimensions at once returns the *pairs*, which is a
          different and much longer table. The design draws the column only where rows carry one
          (``context._has_landing_pages``), so a Search Console table simply has four columns.
        * **no ``group``**. SE Ranking groups keywords because somebody put them in groups.
          Google has no opinion, and inventing themes from substrings would be us making up the
          client's taxonomy.
        * **``volume`` is impressions**, which is what Search Console can actually observe: how
          often the site was *shown* for the term. It is not the same as a keyword tool's
          monthly search volume and is not labelled as if it were.

        ``begin`` is the average position over the comparison window and ``end`` over this one —
        the same "where it started, where it ended" the SE Ranking rows carry, measured over the
        two spans the whole report is already built around rather than over the first and last
        day of the month, which for a low-volume term is two samples and a coin toss.
        """
        rows = await self._query(
            client,
            external_id,
            {
                "startDate": start.isoformat(),
                "endDate": end.isoformat(),
                "dimensions": ["query"],
                "rowLimit": max(limit * 4, 100),
            },
        )
        before: dict[str, float] = {}
        if compare_start is not None and compare_end is not None:
            previous = await self._query(
                client,
                external_id,
                {
                    "startDate": compare_start.isoformat(),
                    "endDate": compare_end.isoformat(),
                    "dimensions": ["query"],
                    "rowLimit": max(limit * 4, 100),
                },
            )
            before = {
                row["keys"][0]: _num(row.get("position"))
                for row in previous
                if row.get("keys")
            }
        out: list[dict[str, Any]] = []
        for row in rows:
            if not row.get("keys"):
                continue
            impressions = _num(row.get("impressions"))
            position = _num(row.get("position"))
            # A term shown twice all month is not a ranking, it is a rounding error, and a
            # table of them buries the ones the client is actually competing for.
            if impressions < min_impressions or not position:
                continue
            query = row["keys"][0]
            start_position = before.get(query)
            begin = int(round(start_position)) if start_position else 0
            finish = int(round(position))
            # Visible at **either** end — ``SeRankingAdapter.keyword_rows``' own rule, kept
            # identical so the two sources produce comparable tables. It also keeps the row a
            # client most needs to see: a term that has *dropped out* of the visible depth is
            # news, and a rule reading only the current position would delete exactly that.
            if not (0 < finish <= max_position or 0 < begin <= max_position):
                continue
            out.append(
                {
                    "keyword": query,
                    "group": "",
                    "begin": begin,
                    "end": finish,
                    # Positive = climbed, matching SE Ranking's convention: rank 8 → 3 is +5
                    # even though the number fell.
                    "change": (begin - finish) if begin else 0,
                    "status": (
                        "new"
                        if not begin
                        else "improved"
                        if finish < begin
                        else "declined"
                        if finish > begin
                        else "stable"
                    ),
                    "landing_page": None,
                    "volume": int(round(impressions)),
                    "clicks": _num(row.get("clicks")),
                }
            )
        # Best first: a client reads the top of the table and stops, so the top of the table has
        # to be where they rank, not wherever Google happened to order the response.
        out.sort(key=lambda item: (item["end"] or 999, -item["volume"]))
        return out[:limit]

    def deep_link(self, external_id: str, config: dict) -> str:
        return f"https://search.google.com/search-console?resource_id={quote(external_id, safe='')}"


register(GSCAdapter())
