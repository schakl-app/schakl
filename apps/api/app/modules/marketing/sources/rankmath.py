"""Rank Math AI Visibility adapter — read through the client's own WordPress (docs/WORDPRESS.md).

The fifth source, and the first whose credential is per **website** rather than per agency or
per user (:data:`~app.modules.marketing.sources.base.AUTH_SITE_KEY`). The client handed to every
method below is a ``WordPressClient`` built by the ``wordpress`` module through
``app/core/wordpress.py`` — this file never names that module (§6) and never sees the password.

Three things a plausible-looking implementation would get wrong, all verified against the plugin
source (``seo-by-rank-math`` 1.0.275) rather than remembered:

* **The ability is not the REST route.** ``rank-math/get-ai-visibility-overview`` reads a
  12-hour ``wp_options`` cache and *cannot* force an upstream fetch — its own ``refresh`` input
  is documented as bypassing the cache and is used only for telemetry
  (``class-get-ai-visibility-overview.php``). Only ``GET /rankmath/v1/ai-visibility/overview``
  reaches Rank Math's backend, and only with ``refresh=1``. A sync built on the newer-looking
  surface would chart a number that moves when somebody opens the WordPress dashboard.
* **There is no history.** Every upstream path is "latest" — there is no date range to ask for,
  and :meth:`fetch_daily` therefore ignores ``start`` entirely and writes *one* row, for the
  most recent day in the window. The trend line exists because we store snapshots, not because
  Rank Math has one.
* **The cadence is not daily.** Analyses run on Rank Math's own schedule per plan, so
  consecutive snapshots legitimately repeat. ``last_analyzed`` is carried into the stored
  metrics so a report can say what it is actually comparing rather than announcing a 0% week.

Every parse is defensive, and none of it has met a live site with a Content AI subscription —
``docs/WORDPRESS.md`` §1 carries the checklist for the day one exists (``docs/OXXA.md``'s
posture, for the same reason).
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from app.modules.marketing.models import MarketingSource
from app.modules.marketing.sources.base import (
    AUTH_SITE_KEY,
    AccountOption,
    DailyMetrics,
    DrilldownRow,
    DrilldownTable,
    register,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.integrations.wordpress.client import WordPressClient

logger = logging.getLogger("schakl.marketing")

#: Where "open in Rank Math" sends the marketeer for the real analysis.
ADMIN_PATH = "/wp-admin/admin.php?page=rank-math-ai-visibility"


def _num(raw: Any) -> float | None:
    """A metric value, or ``None`` where Rank Math has not computed one yet.

    ``None`` rather than ``0.0``: a brand mid-analysis carries nulls where numbers will be, and
    storing a zero would draw a line to the floor and back — a visible, wrong claim about a
    client's visibility, made out of a value nobody reported.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _brands(payload: Any) -> list[dict]:
    """The brand rows inside an ``/overview`` response, whichever envelope it arrived in.

    ``{success, data:{brands}}`` is what the controller sends today. Accepting a bare
    ``{brands}`` too costs one line and avoids the failure mode that matters: reading the
    wrapper wrong turns a good payload into "this client has no brands", which is
    indistinguishable from the truth on a screen.
    """
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    brands = data.get("brands")
    if isinstance(brands, dict):  # the raw upstream shape is {"data": [...]} one level deeper
        brands = brands.get("data")
    return [row for row in brands if isinstance(row, dict)] if isinstance(brands, list) else []


class RankMathAdapter:
    """AI Visibility as tier-1 daily snapshots; competitors and prompts as tier-2 drill-downs."""

    source = MarketingSource.RANKMATH.value
    auth = AUTH_SITE_KEY
    #: No OAuth scope exists for an Application Password. Kept for protocol shape; never checked.
    scope = ""
    drilldowns = ("competitors", "queries")

    # --- picker --------------------------------------------------------------------------- #
    async def list_accounts(self, client: WordPressClient) -> list[AccountOption]:
        """The brands tracked on *this website*, so a client is linked to one explicitly.

        Cache-first (no ``refresh``): choosing which brand to attach is not the moment to spend
        a client's Content AI quota on a fresh upstream analysis.
        """
        payload = await client.ai_visibility_overview()
        options: list[AccountOption] = []
        for brand in _brands(payload):
            brand_id = brand.get("id") or brand.get("uuid")
            if not brand_id:
                continue
            url = str(brand.get("url") or "")
            options.append(
                AccountOption(
                    external_id=str(brand_id),
                    display_name=str(brand.get("name") or brand_id),
                    config={"url": url, "locale": brand.get("locale") or ""},
                    account_hint=url,
                )
            )
        return sorted(options, key=lambda option: option.display_name.lower())

    # --- tier 1 --------------------------------------------------------------------------- #
    async def fetch_daily(
        self,
        client: WordPressClient,
        external_id: str,
        start: date,
        end: date,
        config: dict,
    ) -> list[DailyMetrics]:
        """One snapshot, stamped ``end``. ``start`` is deliberately unused.

        Every other adapter answers a *range* because its provider stores one. Rank Math does
        not: there is no history endpoint anywhere in the plugin, so asking for thirty days
        would either return today's numbers thirty times (a flat line that looks like data) or
        require inventing dates for values that were never measured on them. Writing one row
        for ``end`` is the honest shape, and the backfill therefore has nothing to backfill —
        which is why the first month of a new link is a short chart rather than a wrong one.

        ``refresh=1`` is what makes this a *measurement* rather than a re-read of a cache
        somebody's browser last filled. See the module docstring.
        """
        payload = await client.ai_visibility_overview(refresh=True)
        for brand in _brands(payload):
            if str(brand.get("id") or brand.get("uuid") or "") != external_id:
                continue
            metrics: dict[str, float] = {}
            for key, source_key in (
                ("ai_visibility_score", "score"),
                ("mentions", "mentions"),
                ("citations", "citations"),
                ("avg_sentiment", "avg_sentiment"),
                ("brand_rank", "rank"),
            ):
                value = _num(brand.get(source_key))
                if value is not None:
                    metrics[key] = value
            if not metrics:
                # The brand exists but has no analysis yet. Storing an empty row would claim
                # we measured nothing, when what happened is that nothing has been measured.
                return []
            last_analyzed = brand.get("last_analyzed")
            if isinstance(last_analyzed, str) and last_analyzed:
                # Not a metric — a fact *about* the metrics, carried so a report can say what
                # it is comparing instead of announcing a 0% week between two snapshots of the
                # same analysis (#312's rule: a claim about two spans names both).
                metrics["last_analyzed"] = last_analyzed  # type: ignore[assignment]
            return [DailyMetrics(day=end, metrics=metrics)]

        logger.info("rankmath brand %s is no longer tracked on this site", external_id)
        return []

    # --- tier 2 --------------------------------------------------------------------------- #
    async def drilldown(
        self,
        client: WordPressClient,
        external_id: str,
        kind: str,
        start: date,
        end: date,
        config: dict,
    ) -> DrilldownTable:
        """Live detail, never stored: who the AI mentions instead, and on which prompts."""
        if kind == "queries":
            payload = await client.ai_visibility_queries(external_id)
            rows = payload.get("queries")
            return DrilldownTable(
                kind="queries",
                columns=["enabled"],
                rows=[
                    DrilldownRow(
                        label=str(row.get("text") or ""),
                        metrics={"enabled": 1.0 if row.get("enabled") else 0.0},
                    )
                    for row in (rows if isinstance(rows, list) else [])
                    if isinstance(row, dict) and row.get("text")
                ],
            )

        payload = await client.ai_visibility_insights(external_id)
        rows = payload.get("competitors")
        return DrilldownTable(
            kind="competitors",
            columns=["mentions", "avg_sentiment"],
            rows=[
                DrilldownRow(
                    label=str(row.get("name") or ""),
                    metrics={
                        "mentions": _num(row.get("mentions")) or 0.0,
                        "avg_sentiment": _num(row.get("avg_sentiment")) or 0.0,
                    },
                    href=str(row.get("url")) if row.get("url") else None,
                )
                for row in (rows if isinstance(rows, list) else [])
                if isinstance(row, dict) and row.get("name")
            ],
        )

    def deep_link(self, external_id: str, config: dict) -> str:
        """Rank Math's own AI Visibility screen on the client's site.

        Built from the brand's URL rather than the credential's ``base_url``, which this
        adapter never sees: the client carries the password and the adapter carries the data,
        and keeping them apart is the point of the seam.
        """
        site = str(config.get("url") or "").rstrip("/")
        return f"{site}{ADMIN_PATH}" if site else ADMIN_PATH


register(RankMathAdapter())
