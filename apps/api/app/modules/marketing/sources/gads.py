"""Google Ads adapter — Google Ads REST API (``adwords`` scope + a developer token).

Unlike GA4/GSC this one is **not** a plain ``www.googleapis.com`` call: it lives on
``googleads.googleapis.com``, needs a per-agency ``developer-token`` header (Basic access reads
your own accounts), and a ``login-customer-id`` header when the account sits under a manager.

**Manager accounts (MCC) are the normal agency shape, not an edge case.** Access is granted to
the manager, so ``listAccessibleCustomers`` — which answers *direct* grants only — returns the
MCC and nothing else, and a picker built on it is empty of every client the agency actually
runs. :meth:`GAdsAdapter.list_accounts` expands each manager into its hierarchy and stamps each
child with ``config["manager_id"]``, which :func:`_headers` turns into the ``login-customer-id``
every later call needs. A link made before this existed carries no ``manager_id``: remove it
(the ✕ on its chip) and pick the account again — the picker hides accounts already linked to
the client, so the removal is what puts it back in the list.
The OAuth bearer still comes from ``acting_as``; the developer token is **per-org settings** the
service binds around each Ads call via :func:`developer_token_scope` (with the legacy
``SCHAKL_GOOGLE_ADS_DEVELOPER_TOKEN`` env var as a fallback). With no token the module stays fully
presentable — the picker and sync degrade to a labelled "Ads not configured" state instead of
erroring (epic #134: "keep the module fully presentable with Ads still pending").
"""

from __future__ import annotations

import contextvars
import logging
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

logger = logging.getLogger("schakl.marketing")

API_HOST = "https://googleads.googleapis.com"

#: The version this release is built against. Google sunsets an Ads API version roughly a year
#: after it ships and then answers **404** on every path under it — which is not a credential
#: problem, an account problem or a scope problem, so nothing in the picker's teaching states
#: fits it and the module simply looks broken (v18 sunset 2025-08-20). Overridable per install
#: via ``SCHAKL_GOOGLE_ADS_API_VERSION`` so a box that outlives this release can be bumped from
#: its compose file; keep this constant current anyway — the env var is the escape hatch, not
#: the plan.
DEFAULT_API_VERSION = "v25"


def api_base() -> str:
    """``https://googleads.googleapis.com/<version>`` — resolved per call, never at import, so
    the version is a setting an operator can change without rebuilding the image."""
    version = (settings.google_ads_api_version or DEFAULT_API_VERSION).strip().strip("/")
    return f"{API_HOST}/{version}"


#: How many client accounts one manager (MCC) contributes to the picker. An agency MCC holds
#: tens; a reseller's holds thousands, and an unbounded read behind a combobox is how a picker
#: becomes a timeout (CLAUDE.md §9: every unbounded read is capped). Over the cap is logged,
#: never silently dropped.
MAX_MANAGER_CHILDREN = 500


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
        """Every *client* account this login can report on, MCC hierarchies expanded.

        ``listAccessibleCustomers`` answers only what the Google user has been granted
        **directly**, and an agency's user is granted the manager account — so the raw list is
        one MCC and the picker is empty of every client under it. Each manager is therefore
        walked with a ``customer_client`` query, which returns the whole tree beneath it in one
        call, and each child is tagged with the manager it must be reached through
        (``config["manager_id"]`` → the ``login-customer-id`` header on every later call).

        Manager accounts are not themselves offered: Google refuses metric queries against one,
        so linking it would produce a permanently erroring link rather than a roll-up.
        """
        headers = {"developer-token": _developer_token()}
        resp = await client.get(f"{api_base()}/customers:listAccessibleCustomers", headers=headers)
        resp.raise_for_status()
        options: list[AccountOption] = []
        # A user granted both an MCC and one of its clients directly would otherwise see that
        # client twice, under two different configs.
        seen: set[str] = set()
        for resource in resp.json().get("resourceNames", []):
            customer_id = resource.split("/")[-1]
            # One call per *accessible* customer (usually one MCC), not per client account.
            name, currency, is_manager = await self._customer_meta(client, customer_id, headers)
            candidates = (
                await self._manager_children(client, customer_id, name or customer_id)
                if is_manager
                else [
                    AccountOption(
                        external_id=customer_id,
                        display_name=name or customer_id,
                        config={"currency": currency},
                        account_hint=customer_id,
                    )
                ]
            )
            for option in candidates:
                if option.external_id in seen:
                    continue
                seen.add(option.external_id)
                options.append(option)
        return options

    async def _manager_children(
        self, client: AsyncOAuth2Client, manager_id: str, manager_name: str
    ) -> list[AccountOption]:
        """The enabled, non-manager accounts anywhere under ``manager_id`` — one query.

        ``customer_client`` returns the manager's whole hierarchy, not just its direct children,
        so a nested MCC needs no recursion. Sub-managers are filtered out and their clients kept:
        every one of them is still reachable with this top-level manager as
        ``login-customer-id``, which is what makes one tag per child correct.

        Capped, and the cap is *reported* rather than silently swallowed (docs/PERFORMANCE.md,
        and CLAUDE.md §17's rule against quiet truncation): a picker that lists 500 of an
        agency's 900 accounts and says nothing looks exactly like an agency with 500 accounts.
        Returned sorted by name, which is the order the combobox should read in.
        """
        config = {"manager_id": manager_id}
        # No ORDER BY: the sort is done here instead, so the query leans on nothing beyond the
        # two filters every GAQL guide documents. The LIMIT is one over the cap, purely so the
        # "there were more" case is detectable rather than guessed at.
        query = (
            "SELECT customer_client.id, customer_client.descriptive_name, "
            "customer_client.currency_code "
            "FROM customer_client "
            "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED' "
            f"LIMIT {MAX_MANAGER_CHILDREN + 1}"
        )
        rows = await self._search(client, manager_id, query, config)
        if len(rows) > MAX_MANAGER_CHILDREN:
            logger.warning(
                "Google Ads manager %s has more than %s client accounts; the picker lists %s of "
                "them",
                manager_id,
                MAX_MANAGER_CHILDREN,
                MAX_MANAGER_CHILDREN,
            )
            rows = rows[:MAX_MANAGER_CHILDREN]
        options: list[AccountOption] = []
        for row in rows:
            child = row.get("customerClient", {})
            child_id = str(child.get("id") or "")
            if not child_id:
                continue
            options.append(
                AccountOption(
                    external_id=child_id,
                    display_name=child.get("descriptiveName") or child_id,
                    # `manager_id` is the load-bearing half: without it every later call to this
                    # account is made as a user who has no direct grant on it, and 403s.
                    config={"currency": child.get("currencyCode", ""), "manager_id": manager_id},
                    account_hint=f"{child_id} · {manager_name}",
                )
            )
        options.sort(key=lambda option: option.display_name.casefold())
        return options

    async def _customer_meta(
        self, client: AsyncOAuth2Client, customer_id: str, headers: dict[str, str]
    ) -> tuple[str, str, bool]:
        """``(name, currency, is_manager)`` for one directly-accessible customer.

        ``customer.manager`` is what separates an MCC from an ordinary advertiser, and there is
        no other way to tell: ``listAccessibleCustomers`` returns bare ids.
        """
        resp = await client.post(
            f"{api_base()}/customers/{customer_id}/googleAds:search",
            headers=headers,
            json={
                "query": (
                    "SELECT customer.descriptive_name, customer.currency_code, customer.manager "
                    "FROM customer"
                )
            },
        )
        if resp.status_code != 200:
            return "", "", False
        results = resp.json().get("results", [])
        if not results:
            return "", "", False
        customer = results[0].get("customer", {})
        return (
            customer.get("descriptiveName", ""),
            customer.get("currencyCode", ""),
            bool(customer.get("manager", False)),
        )

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
                f"{api_base()}/customers/{customer_id}/googleAds:search",
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
