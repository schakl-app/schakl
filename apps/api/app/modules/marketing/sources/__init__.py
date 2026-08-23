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

**Tag Manager is not here, and that is a decision rather than an omission** (#411). The team
asked for it in the same picker as the other five, which is right about the *control* and wrong
about the *vocabulary*: a container has no marketeer-facing metric of its own — no
``list_accounts`` worth caching, no ``fetch_daily``, no drill-down, and the conversions it fires
already arrive through GA4. A sixth ``MarketingSource`` would therefore need a value that
``METRICS_BY_SOURCE``, ``SCOPE_BY_SOURCE``, :func:`primary_metric`, ``aggregate``, the overview
grid, the report sections and the nightly sync each have to be taught to say nothing about — and
would still put a section on a client's dashboard that draws no numbers, which is precisely what
reads as broken.

So the connect control offers **two lists**: these five metric sources, and the *connections*
(``MarketingConnection``, ``schemas.py``), which today is Tag Manager alone. A connection is
attached through its own module's route and there is no marketing row behind it — the
``gtm_containers`` row *is* the link. That is a stronger form of #338's "the two must not
disagree" than mirroring: two rows cannot disagree when there is only one. The rule for the
next one is the same question this file has answered twice: **if it has no daily number, it is a
connection, not a source.**
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
