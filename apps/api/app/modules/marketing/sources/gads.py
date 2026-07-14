"""Google Ads adapter — Google Ads REST API (``adwords`` scope + a developer token).

Unlike GA4/GSC this one is **not** a plain ``www.googleapis.com`` call: it lives on
``googleads.googleapis.com``, needs a per-agency ``developer-token`` header (Basic access reads
your own accounts), and a ``login-customer-id`` header when the account sits under a manager.
The OAuth bearer still comes from ``acting_as``; the developer token is **per-org settings** the
service binds around each Ads call via :func:`developer_token_scope` (with the legacy
``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var as a fallback). With no token the module stays fully
presentable — the picker and sync degrade to a labelled "Ads not configured" state instead of
erroring (epic #134: "keep the module fully presentable with Ads still pending").
"""

from __future__ import annotations

import contextvars
from datetime import date
from typing import TYPE_CHECKING, Any

from app.config import settings
from app.modules.google.oauth import SCOPE_ADS
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

API = "https://googleads.googleapis.com/v18"


class AdsNotConfigured(Exception):
    """Raised when no developer token is set — a presentable state, not a bug."""


#: The acting org's Google Ads developer token, bound by the service around each Ads call so this
#: stateless adapter reads a *per-org* secret without threading it through the shared source
#: protocol (whose ``list_accounts`` has no ``config`` to carry it). Falls back to the deprecated
#: env var when unset — the two-way door that keeps existing installs working.
_developer_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "marketing_ads_developer_token", default=None
)


class developer_token_scope:  # noqa: N801 — a context manager, named like `contextlib` ones
    """Bind ``token`` as the Ads developer token for the adapter calls made inside the block."""

    def __init__(self, token: str | None) -> None:
        self._token = token
        self._reset: contextvars.Token[str | None] | None = None

    def __enter__(self) -> developer_token_scope:
        self._reset = _developer_token_var.set(self._token)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._reset is not None:
            _developer_token_var.reset(self._reset)


def _num(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _developer_token() -> str:
    # The bound per-org token wins; the env var is the fallback for installs not yet migrated.
    token = _developer_token_var.get() or settings.google_ads_developer_token
    if not token:
        raise AdsNotConfigured
    return token


def _headers(config: dict) -> dict[str, str]:
    headers = {"developer-token": _developer_token()}
    manager_id = str(config.get("manager_id") or "").replace("-", "")
    if manager_id:
        headers["login-customer-id"] = manager_id
    return headers


class GAdsAdapter:
    source = MarketingSource.GADS.value
    scope = SCOPE_ADS
    drilldowns = ("campaigns",)

    async def list_accounts(self, client: AsyncOAuth2Client) -> list[AccountOption]:
        headers = {"developer-token": _developer_token()}
        resp = await client.get(f"{API}/customers:listAccessibleCustomers", headers=headers)
        resp.raise_for_status()
        options: list[AccountOption] = []
        for resource in resp.json().get("resourceNames", []):
            customer_id = resource.split("/")[-1]
            # One extra call per customer to resolve the human name + currency.
            name, currency = await self._customer_meta(client, customer_id, headers)
            options.append(
                AccountOption(
                    external_id=customer_id,
                    display_name=name or customer_id,
                    config={"currency": currency},
                    account_hint=customer_id,
                )
            )
        return options

    async def _customer_meta(
        self, client: AsyncOAuth2Client, customer_id: str, headers: dict[str, str]
    ) -> tuple[str, str]:
        resp = await client.post(
            f"{API}/customers/{customer_id}/googleAds:search",
            headers=headers,
            json={
                "query": "SELECT customer.descriptive_name, customer.currency_code FROM customer"
            },
        )
        if resp.status_code != 200:
            return "", ""
        results = resp.json().get("results", [])
        if not results:
            return "", ""
        customer = results[0].get("customer", {})
        return customer.get("descriptiveName", ""), customer.get("currencyCode", "")

    async def _search(
        self, client: AsyncOAuth2Client, customer_id: str, query: str, config: dict
    ) -> list[dict]:
        results: list[dict] = []
        page_token: str | None = None
        while True:
            body: dict[str, Any] = {"query": query}
            if page_token:
                body["pageToken"] = page_token
            resp = await client.post(
                f"{API}/customers/{customer_id}/googleAds:search",
                headers=_headers(config),
                json=body,
            )
            resp.raise_for_status()
            data = resp.json()
            results.extend(data.get("results", []))
            page_token = data.get("nextPageToken")
            if not page_token:
                break
        return results

    async def fetch_daily(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        start: date,
        end: date,
        config: dict,
    ) -> list[DailyMetrics]:
        query = (
            "SELECT segments.date, metrics.cost_micros, metrics.clicks, metrics.impressions, "
            "metrics.conversions, metrics.conversions_value FROM customer "
            f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}'"
        )
        rows = await self._search(client, external_id, query, config)
        currency = config.get("currency")
        by_day: dict[date, dict[str, float]] = {}
        for row in rows:
            day = date.fromisoformat(row["segments"]["date"])
            m = row.get("metrics", {})
            bucket = by_day.setdefault(
                day,
                {
                    "cost": 0.0, "clicks": 0.0, "impressions": 0.0,
                    "conversions": 0.0, "conversionsValue": 0.0,
                },
            )
            bucket["cost"] += _num(m.get("costMicros")) / 1_000_000
            bucket["clicks"] += _num(m.get("clicks"))
            bucket["impressions"] += _num(m.get("impressions"))
            bucket["conversions"] += _num(m.get("conversions"))
            bucket["conversionsValue"] += _num(m.get("conversionsValue"))
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
        query = (
            "SELECT campaign.name, metrics.cost_micros, metrics.clicks, metrics.conversions, "
            "metrics.conversions_value FROM campaign "
            f"WHERE segments.date BETWEEN '{start.isoformat()}' AND '{end.isoformat()}' "
            "ORDER BY metrics.cost_micros DESC LIMIT 10"
        )
        rows = await self._search(client, external_id, query, config)
        columns = ["cost", "clicks", "conversions", "conversionsValue"]
        out: list[DrilldownRow] = []
        for row in rows:
            m = row.get("metrics", {})
            out.append(
                DrilldownRow(
                    label=row.get("campaign", {}).get("name", ""),
                    metrics={
                        "cost": _num(m.get("costMicros")) / 1_000_000,
                        "clicks": _num(m.get("clicks")),
                        "conversions": _num(m.get("conversions")),
                        "conversionsValue": _num(m.get("conversionsValue")),
                    },
                )
            )
        return DrilldownTable(kind="campaigns", columns=columns, rows=out)

    def deep_link(self, external_id: str, config: dict) -> str:
        return f"https://ads.google.com/aw/overview?__c={external_id}"


register(GAdsAdapter())
