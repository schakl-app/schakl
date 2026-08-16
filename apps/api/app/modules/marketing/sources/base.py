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

from app.integrations.google.oauth import SCOPE_ADS, SCOPE_ANALYTICS, SCOPE_SEARCH_CONSOLE
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
#: SE Ranking (#300). ``avg_position`` leads because it is the number a client asks about, and
#: it is both *averaged* and *lower-is-better* — registered in both sets below, or a month of
#: daily averages would be summed into a four-figure "position".
SERANKING_METRICS = [
    "avg_position",
    "top3",
    "top10",
    "top30",
    "keywords_ranking",
    "keywords_tracked",
]
#: Rank Math AI Visibility, read through the client's own WordPress (docs/WORDPRESS.md).
#: ``ai_visibility_score`` leads because it is the number a client asks about — and because
#: this source is the client-facing AI-visibility figure, with SE Ranking's ``ai_search`` left
#: as the per-LLM drill-down it already was. Two vendors' scores presented as one dashboard
#: number is not a screen anyone can summarise (#312), so only one of them is a tile.
#:
#: Every one of these is a **snapshot**, not a daily total: Rank Math analyses on its own
#: cadence and reports the latest state, so all five are registered in
#: :data:`AVERAGED_METRICS` below. Summing a month of them would produce a four-figure
#: "visibility score" — the trap ``avg_position`` already documents, with five ways to fall in.
RANKMATH_METRICS = [
    "ai_visibility_score",
    "mentions",
    "citations",
    "avg_sentiment",
    "brand_rank",
]

#: The GA4 acquisition split we store as a sub-object (sessions by default channel group).
GA4_CHANNELS = ["Organic Search", "Paid Search", "Direct", "Organic Social", "Referral", "Email"]

METRICS_BY_SOURCE: dict[str, list[str]] = {
    MarketingSource.GA4.value: GA4_METRICS,
    MarketingSource.GSC.value: GSC_METRICS,
    MarketingSource.GADS.value: GADS_METRICS,
    MarketingSource.SERANKING.value: SERANKING_METRICS,
    MarketingSource.RANKMATH.value: RANKMATH_METRICS,
}

#: Metrics that are *averages*, not sums — a period total re-derives them, never adds them.
#: (CTR and average position over N days is not the sum of N daily CTRs.)
#:
#: Every Rank Math metric is here, including ``mentions`` and ``citations``, which *look* like
#: counts and are not: Rank Math reports a brand's running totals as of its last analysis, so
#: two consecutive daily snapshots of "18 mentions" mean eighteen mentions, not thirty-six.
#: They are counts of a thing, stored as a level.
AVERAGED_METRICS = {
    "ctr",
    "position",
    "engagementRate",
    "avg_position",
    "ai_visibility_score",
    "mentions",
    "citations",
    "avg_sentiment",
    "brand_rank",
}

#: Metrics where a *lower* number is better, so a positive delta reads red not green (position).
LOWER_IS_BETTER = {"position", "avg_position", "brand_rank"}


def primary_metric(source: str) -> str:
    return METRICS_BY_SOURCE[source][0]


#: How a source authenticates (#300). Not a generalisation of Google's flow — the opposite:
#: Google's *is* the special one, with a per-user grant, incremental scopes, a revocable
#: connection and a reconnect prompt. An org-key source has none of those states; it is
#: configured or it is not, and conflating the two would have the SE Ranking card telling an
#: admin to "reconnect Google".
AUTH_GOOGLE = "google"
AUTH_ORG_KEY = "org_key"
#: One credential per **website** (docs/WORDPRESS.md) — therefore per *link*, not per org and
#: not per user. Rank Math AI Visibility is read through a WordPress Application Password that
#: belongs to one client's site, so there is no agency-wide key to fall back on and no
#: connection to reconnect. Its failure states are its own ("this site's password was revoked",
#: "Rank Math is not connected to a Content AI subscription"), which is the same argument #300
#: made for splitting ``AUTH_ORG_KEY`` out of ``AUTH_GOOGLE`` — made a second time, in the same
#: shape. That repetition is why :func:`app.modules.marketing.service.client_for_link` exists:
#: the third kind was the point at which per-kind ``if`` branches at five call sites stopped
#: being cheaper than one dispatch.
AUTH_SITE_KEY = "site_key"


class MarketingSourceAdapter(Protocol):
    """What every source must implement. Stateless — the prepared client is passed in."""

    source: str
    #: Which credential this source rides (:data:`AUTH_GOOGLE` / :data:`AUTH_ORG_KEY`).
    auth: str
    #: The OAuth scope a connection must hold to use this source. Empty for an org-key source.
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
    # SE Ranking has no OAuth and therefore no scope. Present with an empty value rather than
    # absent, so a caller iterating the sources gets a falsy answer instead of a KeyError.
    MarketingSource.SERANKING.value: "",
    # Rank Math rides a per-website WordPress credential — no OAuth, so no scope either.
    MarketingSource.RANKMATH.value: "",
}


def source_auth(source: str) -> str:
    """Which credential a source rides. Defaults to Google so an adapter that predates the
    distinction keeps working unchanged."""
    return getattr(SOURCES.get(source), "auth", AUTH_GOOGLE)


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
