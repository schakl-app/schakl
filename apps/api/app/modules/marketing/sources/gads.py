"""Google Ads adapter — a thin consumer of :mod:`app.core.googleads`.

Everything Ads-specific that used to live here (the version in the URL, the ``developer-token``
and ``login-customer-id`` headers, the paging, the error model) moved into core when
``google_ads`` was built, because two modules needed it and §6 forbids either importing the
other. What is left here is what marketing actually has an opinion about: **which five numbers a
dashboard tile draws**, and which campaigns the drill-down shows.

The account itself is the ``google_ads`` module's to own now. This adapter still reads its
``external_id`` and ``config["manager_id"]`` off the link — deliberately, and it is not a second
truth. ``MarketingLink.google_ads_account_id`` is the authority; ``external_id`` is a display
copy the panel prints and ``deep_link`` builds from, and the *call parameters* are resolved
through the seam at call time when the FK is set. When it is not — an instance that never
enabled ``google_ads``, or a link made before it existed — the stored values still answer, which
is what makes the whole thing degrade to exactly today's behaviour instead of to an error.

``AdsNotConfigured`` is re-exported and still means what it always did: a presentable "Ads is not
set up yet", drawn by the picker, the tile and the sync alike.
"""

from __future__ import annotations

import contextvars
import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from app.core.googleads import (
    DEFAULT_API_VERSION,
    AdsClient,
    AdsCredentials,
    AdsNotConfigured,
    api_base,
    normalise_customer_id,
)
from app.core.googleads.client import API_HOST
from app.modules.google.oauth import SCOPE_ADS
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

logger = logging.getLogger("schakl.marketing")

# Re-exported so the module's existing callers and tests keep their import site. The definitions
# are core's — a second copy of "which Ads API version is current" is exactly the drift this
# module's move to the seam exists to prevent.
__all__ = [
    "API_HOST",
    "DEFAULT_API_VERSION",
    "MAX_MANAGER_CHILDREN",
    "AdsNotConfigured",
    "GAdsAdapter",
    "api_base",
    "developer_token_scope",
]

#: How many client accounts one manager contributes to the picker. See the google_ads module's
#: constant of the same name; kept here because this picker is a separate surface with the same
#: rule, and an unbounded read behind a combobox is a build break either way.
MAX_MANAGER_CHILDREN = 500


#: The acting org's Google Ads developer token, bound by the service around each Ads call so this
#: stateless adapter reads a *per-org* secret without threading it through the shared source
#: protocol (whose ``list_accounts`` has no ``config`` to carry it).
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
    token = _developer_token_var.get()
    if not token:
        raise AdsNotConfigured("no google ads developer token configured")
    return token


def _client(http: AsyncOAuth2Client, config: dict, *, tool: str = "") -> AdsClient:
    """An :class:`AdsClient` for the account this link names.

    ``manager_id`` is the load-bearing half of ``config``: an agency's Google user is granted the
    *manager*, so without the ``login-customer-id`` header every call against a client account is
    made by someone holding no grant on it and 403s. A link made before that was understood
    carries none — remove it and pick the account again.
    """
    manager = normalise_customer_id(config.get("manager_id")) or None
    return AdsClient(
        http,
        AdsCredentials(developer_token=_developer_token(), login_customer_id=manager),
        tool=tool,
    )


class GAdsAdapter:
    source = MarketingSource.GADS.value
    auth = AUTH_GOOGLE
    scope = SCOPE_ADS
    drilldowns = ("campaigns",)

    async def list_accounts(self, client: AsyncOAuth2Client) -> list[AccountOption]:
        """Every *client* account this login can report on, MCC hierarchies expanded.

        ``listAccessibleCustomers`` answers only what the Google user has been granted
        **directly**, and an agency's user is granted the manager account, so the raw list is one
        MCC and the picker is empty of every client under it. Each manager is walked with one
        ``customer_client`` query, and each child is tagged with the manager it must be reached
        through. Manager accounts are not themselves offered: Google refuses metric queries
        against one, so linking it would produce a permanently erroring link rather than a
        roll-up.
        """
        # No manager header while discovering: these are the *directly* granted customers, and
        # naming a login-customer-id we have not established yet is how discovery 403s itself.
        ads = _client(client, {}, tool="picker")
        options: list[AccountOption] = []
        seen: set[str] = set()
        for customer_id in await ads.accessible_customers():
            name, currency, is_manager = await self._customer_meta(ads, customer_id)
            if is_manager:
                candidates = await self._manager_children(client, customer_id, name or customer_id)
            else:
                candidates = [
                    AccountOption(
                        external_id=customer_id,
                        display_name=name or customer_id,
                        config={"currency": currency},
                        account_hint=customer_id,
                    )
                ]
            for option in candidates:
                # A user granted both an MCC and one of its clients directly would otherwise see
                # that client twice, under two different configs.
                if option.external_id in seen:
                    continue
                seen.add(option.external_id)
                options.append(option)
        return options

    async def _manager_children(
        self, client: AsyncOAuth2Client, manager_id: str, manager_name: str
    ) -> list[AccountOption]:
        """The enabled, non-manager accounts anywhere under ``manager_id`` — one query.

        ``customer_client`` returns the whole hierarchy, so a nested MCC needs no recursion.
        Sub-managers are dropped and their clients kept: every one is still reachable with this
        top-level manager as ``login-customer-id``.

        Capped, and the cap is *reported* rather than swallowed: a picker that lists 500 of an
        agency's 900 accounts and says nothing looks exactly like an agency with 500 accounts.
        """
        config = {"manager_id": manager_id}
        query = (
            "SELECT customer_client.id, customer_client.descriptive_name, "
            "customer_client.currency_code "
            "FROM customer_client "
            "WHERE customer_client.manager = FALSE AND customer_client.status = 'ENABLED' "
            # One over the cap, purely so "there were more" is detectable rather than guessed at.
            f"LIMIT {MAX_MANAGER_CHILDREN + 1}"
        )
        rows = await _client(client, config, tool="picker").search(
            manager_id, query, context="manager_children"
        )
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
            child_id = normalise_customer_id(child.get("id"))
            if not child_id:
                continue
            options.append(
                AccountOption(
                    external_id=child_id,
                    display_name=child.get("descriptiveName") or child_id,
                    config={"currency": child.get("currencyCode", ""), "manager_id": manager_id},
                    account_hint=f"{child_id} · {manager_name}",
                )
            )
        options.sort(key=lambda option: option.display_name.casefold())
        return options

    async def _customer_meta(
        self, ads: AdsClient, customer_id: str
    ) -> tuple[str, str, bool]:
        """``(name, currency, is_manager)`` for one directly-accessible customer.

        ``customer.manager`` is what separates an MCC from an ordinary advertiser, and
        ``listAccessibleCustomers`` returns bare ids. A customer that refuses is skipped rather
        than failing the whole picker: one revoked grant among ten must not empty it.
        """
        try:
            row = await ads.search_one(
                customer_id,
                "SELECT customer.descriptive_name, customer.currency_code, customer.manager "
                "FROM customer",
                context="customer_meta",
            )
        except Exception:  # noqa: BLE001 — one unreadable customer is not a broken picker
            return "", "", False
        if row is None:
            return "", "", False
        customer = row.get("customer", {})
        return (
            customer.get("descriptiveName", ""),
            customer.get("currencyCode", ""),
            bool(customer.get("manager", False)),
        )

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
        rows = await _client(client, config, tool="sync").search(
            external_id, query, context="fetch_daily"
        )
        currency = config.get("currency")
        by_day: dict[date, dict[str, float]] = {}
        for row in rows:
            day = date.fromisoformat(row["segments"]["date"])
            m = row.get("metrics", {})
            bucket = by_day.setdefault(
                day,
                {
                    "cost": 0.0,
                    "clicks": 0.0,
                    "impressions": 0.0,
                    "conversions": 0.0,
                    "conversionsValue": 0.0,
                },
            )
            # costMicros is an int64 and therefore arrives as a JSON *string*; `_num` is what
            # keeps that from silently becoming a concatenation or a zero.
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
        rows = await _client(client, config, tool="drilldown").search(
            external_id, query, context="drilldown"
        )
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
