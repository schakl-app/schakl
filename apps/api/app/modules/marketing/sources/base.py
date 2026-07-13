"""The source-adapter protocol + the metric vocabulary shared across the module.

The metric keys are the contract between the adapters (which write them into
``marketing_metrics_daily.metrics``), the service (which sums/deltas them) and the web (which
labels them via ``marketing.metric.<key>`` i18n keys). Keeping them in one place is what lets a
tenant's overview grid and a client's panel speak the same language.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import TYPE_CHECKING, Protocol

from app.modules.google.oauth import SCOPE_ADS, SCOPE_ANALYTICS, SCOPE_SEARCH_CONSOLE
from app.modules.marketing.models import MarketingSource

if TYPE_CHECKING:
    from authlib.integrations.httpx_client import AsyncOAuth2Client


@dataclass(frozen=True)
class AccountOption:
    """One pickable account/property/site a connection can reach (#132 picker option)."""

    external_id: str
    display_name: str
    #: Extra bits to snapshot onto the link's ``config`` (currency, site type, manager id).
    config: dict = field(default_factory=dict)
    #: The account this belongs to, shown as the combobox ``hint`` so several connected Google
    #: accounts disambiguate without a grouping header (the base Combobox has none).
    account_hint: str | None = None


@dataclass(frozen=True)
class DailyMetrics:
    """One link's metrics for one day, as an adapter produces them."""

    day: date
    metrics: dict[str, float]
    currency: str | None = None


@dataclass(frozen=True)
class DrilldownRow:
    """One row of a live drill-down (a top page, query or campaign)."""

    label: str
    metrics: dict[str, float]
    href: str | None = None


@dataclass(frozen=True)
class DrilldownTable:
    """A named drill-down: its columns (metric keys) and rows, plus a deep link out."""

    kind: str
    columns: list[str]
    rows: list[DrilldownRow]


# --- metric vocabulary, per source ----------------------------------------------------------- #
# The order here is display order; the FIRST entry is the source's "primary" metric — the one a
# sparkline and the overview grid lead with.
GA4_METRICS = [
    "sessions",
    "totalUsers",
    "newUsers",
    "keyEvents",
    "conversions",
    "engagementRate",
    "totalRevenue",
]
GSC_METRICS = ["clicks", "impressions", "ctr", "position"]
GADS_METRICS = ["cost", "clicks", "impressions", "conversions", "conversionsValue"]

#: The GA4 acquisition split we store as a sub-object (sessions by default channel group).
GA4_CHANNELS = ["Organic Search", "Paid Search", "Direct", "Organic Social", "Referral", "Email"]

METRICS_BY_SOURCE: dict[str, list[str]] = {
    MarketingSource.GA4.value: GA4_METRICS,
    MarketingSource.GSC.value: GSC_METRICS,
    MarketingSource.GADS.value: GADS_METRICS,
}

#: Metrics that are *averages*, not sums — a period total re-derives them, never adds them.
#: (CTR and average position over N days is not the sum of N daily CTRs.)
AVERAGED_METRICS = {"ctr", "position", "engagementRate"}

#: Metrics where a *lower* number is better, so a positive delta reads red not green (position).
LOWER_IS_BETTER = {"position"}


def primary_metric(source: str) -> str:
    return METRICS_BY_SOURCE[source][0]


class MarketingSourceAdapter(Protocol):
    """What every source must implement. Stateless — the connection's client is passed in."""

    source: str
    #: The OAuth scope a connection must hold to use this source.
    scope: str
    #: Drill-down kinds this source offers (``marketing.drilldown.<kind>`` i18n).
    drilldowns: tuple[str, ...]

    async def list_accounts(self, client: AsyncOAuth2Client) -> list[AccountOption]:
        """The accounts/properties/sites this connection can reach (picker options)."""
        ...

    async def fetch_daily(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        start: date,
        end: date,
        config: dict,
    ) -> list[DailyMetrics]:
        """Daily aggregates for ``[start, end]`` inclusive — tier 1, stored + upserted."""
        ...

    async def drilldown(
        self,
        client: AsyncOAuth2Client,
        external_id: str,
        kind: str,
        start: date,
        end: date,
        config: dict,
    ) -> DrilldownTable:
        """A live tier-2 drill-down (top pages/queries/campaigns) for the range."""
        ...

    def deep_link(self, external_id: str, config: dict) -> str:
        """Where "open in GA4/GSC/Ads" sends the marketeer for the real analysis."""
        ...


# Populated at import time by each adapter module (avoids a circular import at module top).
SOURCES: dict[str, MarketingSourceAdapter] = {}

# The scope each source rides — mirrored here so the service can answer "does this connection
# carry the grant?" without importing every adapter.
SCOPE_BY_SOURCE: dict[str, str] = {
    MarketingSource.GA4.value: SCOPE_ANALYTICS,
    MarketingSource.GSC.value: SCOPE_SEARCH_CONSOLE,
    MarketingSource.GADS.value: SCOPE_ADS,
}


def source_for(source: str) -> MarketingSourceAdapter:
    from app.errors import AppError

    adapter = SOURCES.get(source)
    if adapter is None:
        raise AppError("validation", "errors.validation", status_code=422)
    return adapter


def register(adapter: MarketingSourceAdapter) -> None:
    SOURCES[adapter.source] = adapter


# A stable id namespace so a metrics cache key never collides across sources.
def cache_namespace(org_id: uuid.UUID, link_id: uuid.UUID, kind: str) -> str:
    return f"schakl:marketing:drill:{org_id}:{link_id}:{kind}"
