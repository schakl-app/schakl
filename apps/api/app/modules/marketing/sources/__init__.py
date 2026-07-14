"""Per-source adapters (GA4 / GSC / Ads) behind one small protocol (epic #134).

Each adapter knows three things and nothing about our tables: how to *list* the accounts a
connection can reach (for the pickers, #132), how to pull one day-range of aggregates
(``fetch_daily``, tier 1 of #133), and how to fetch a live drill-down (tier 2). The service layer
owns storage, caching and tenancy; an adapter only speaks Google. A fourth source later is a new
module here plus one line in :data:`SOURCES` — no schema or service change.
"""

from __future__ import annotations

# Import for the registration side effect: each adapter calls ``register()`` at import time,
# populating :data:`SOURCES`. Ordered GA4 → GSC → Ads, the epic's build order.
from app.modules.marketing.sources import ga4, gads, gsc  # noqa: E402, F401
from app.modules.marketing.sources.base import (
    SOURCES,
    AccountOption,
    DailyMetrics,
    DrilldownTable,
    MarketingSourceAdapter,
    source_for,
)

__all__ = [
    "SOURCES",
    "AccountOption",
    "DailyMetrics",
    "DrilldownTable",
    "MarketingSourceAdapter",
    "source_for",
]
