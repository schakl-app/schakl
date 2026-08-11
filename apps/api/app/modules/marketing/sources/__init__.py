"""Per-source adapters (GA4 / GSC / Ads / SE Ranking) behind one small protocol (epic #134).

Each adapter knows three things and nothing about our tables: how to *list* the accounts a
credential can reach (for the pickers, #132), how to pull one day-range of aggregates
(``fetch_daily``, tier 1 of #133), and how to fetch a live drill-down (tier 2). The service layer
owns storage, caching and tenancy.

The prediction in this docstring — "a fourth source is a new module here plus one line in
``SOURCES``, no service change" — held for the schema and missed one thing (#300): SE Ranking
does not authenticate the way Google does. So an adapter now also declares its ``auth`` kind,
and the service builds the right client for it. That is the whole extension; the storage, the
caching, the metric vocabulary and the panel all took the new source unchanged.

The *fifth* source (``rankmath``, docs/WORDPRESS.md) missed it again, the same way, for a third
kind of credential — one per **website**. Two identical surprises is a pattern rather than a
coincidence, so the per-kind branches at the service's call sites became one dispatch
(``MarketingService`` → ``keyed_client``). The prediction above is now true of everything except
authentication, and authentication has a seam of its own.
"""

from __future__ import annotations

# Import for the registration side effect: each adapter calls ``register()`` at import time,
# populating :data:`SOURCES`. Ordered GA4 → GSC → Ads → SE Ranking, the build order.
from app.modules.marketing.sources import ga4, gads, gsc, rankmath, seranking  # noqa: E402, F401
from app.modules.marketing.sources.base import (
    AUTH_GOOGLE,
    AUTH_ORG_KEY,
    AUTH_SITE_KEY,
    SOURCES,
    AccountOption,
    DailyMetrics,
    DrilldownTable,
    MarketingSourceAdapter,
    source_auth,
    source_for,
)

__all__ = [
    "AUTH_GOOGLE",
    "AUTH_ORG_KEY",
    "AUTH_SITE_KEY",
    "SOURCES",
    "AccountOption",
    "DailyMetrics",
    "DrilldownTable",
    "MarketingSourceAdapter",
    "source_auth",
    "source_for",
]
