"""google_search_console integration — Search Console as a first-class read surface, and as its
own MCP section.

Business-licensed — see LICENSE.

**Why this is not more depth in ``marketing``.** The marketing module already reads Search
Console, and it reads it for one purpose: a nightly four-metric aggregate per linked client,
folded into a dashboard beside GA4, Google Ads, Rank Math and SE Ranking, with three live
drill-downs. That is the right shape for "how is this client doing" and the wrong shape for
every other question anybody asks Search Console — which queries by which page, is this URL
indexed and why not, which sitemap has errors, what happened at 09:00 this morning, is the site
seen in Discover at all. Those need the property's own vocabulary and the two APIs the dashboard
never calls (sitemaps, URL inspection), and the answer to "give agents Search Console" is a
router prefix rather than a hand-written list (the ``google_analytics`` rule: a dedicated tool
group is a router prefix, or it is a list that rots).

So this is an **integration** by §6a's test: it holds no capability of ours, it stores nothing,
and with the vendor gone it is gone rather than poorer. It requires ``google`` because the
credential is a ``google_connections`` row carrying ``webmasters.readonly`` and there is no
second way to obtain one. It deliberately does **not** require ``marketing``: an agency that
wants an agent able to answer Search Console questions should not be made to switch on a
licensed dashboard module it did not ask for, and the two never read each other's rows — the
one thing they share is the URL of the console's Generative AI report, which lives in
``client.py`` so that the dashboard's card and the assistant's tool cannot point two ways.

**The route list is the tool list** (§12). Thirteen GET routes under ``/google-search-console``
*are* ``/mcp/google-search-console``: a dedicated Search Console tool group, self-maintaining,
with no hand-written list of tools to fall out of step with the code. The seven curated
``mcp_tools`` beside them are the in-app assistant's catalog.

Every operation is a **read**. Not a phase: there is nothing in a client's Search Console
property this platform has any business writing, and an all-GET surface keeps answering past a
licence expiry, which is the right way round (§18).
"""

from __future__ import annotations

from app.integrations.google_search_console.mcp import GOOGLE_SEARCH_CONSOLE_MCP_TOOLS
from app.integrations.google_search_console.permissions import (
    GOOGLE_SEARCH_CONSOLE_PERMISSIONS,
)
from app.integrations.google_search_console.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="google_search_console",
    # A conversation with somebody else's service, not a capability of our own (§6a).
    kind=KIND_INTEGRATION,
    # The credential is a `google_connections` row; there is no second way to get one.
    requires=("google",),
    router=router,
    i18n_namespace="google_search_console",
    # Licensed (issue #137), the same bracket as `google_analytics`. Nothing here mutates, so
    # the write gate governs only whether the integration may be newly enabled: an install that
    # already reads Search Console keeps reading it past an expiry, because data is never
    # hostage (epic #140).
    sku="google_search_console",
    permissions=GOOGLE_SEARCH_CONSOLE_PERMISSIONS,
    # The in-app assistant's catalog. The external MCP surface is the router, one level up.
    mcp_tools=GOOGLE_SEARCH_CONSOLE_MCP_TOOLS,
    # **No cron and no models.** Nothing is mirrored: every answer is fetched live under the
    # asking user's own grant. The nightly aggregate an agency wants for a *dashboard* already
    # exists, in `marketing`, and duplicating it here would be a second answer to a question
    # that has one.
)

registry.register(module)
