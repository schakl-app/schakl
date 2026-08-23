"""google_analytics integration — GA4 as a first-class read surface, and as its own MCP section.

Business-licensed — see LICENSE.

**Why this is not more depth in ``marketing``.** The marketing module already reads GA4, and it
reads it for one purpose: a small nightly aggregate per linked client, folded into a dashboard
beside Search Console, Google Ads, Rank Math and SE Ranking. That is the right shape for "how is
this client doing" and it is the wrong shape for every other question anybody asks Analytics —
which pages, which sources, which events, is the tracking even working, what happened at 14:00
yesterday. Those need the property's *own* vocabulary (its custom dimensions, its key events,
its metadata document), which no cross-source dashboard can carry without becoming a GA4 client
with four other sources bolted to it.

So this is an **integration** by §6a's test: it holds no capability of ours, it stores nothing,
and with the vendor gone it is gone rather than poorer. It requires ``google`` because the
credential is a ``google_connections`` row carrying ``analytics.readonly`` and there is no
second way to obtain one. It deliberately does **not** require ``marketing``: an agency that
wants an agent able to answer Analytics questions should not be made to switch on a licensed
dashboard module it did not ask for, and the two never read each other's rows.

**The route list is the tool list** (§12), which is the point of the whole package. Because the
MCP surface is derived from router prefixes, seventeen GET routes under ``/google-analytics``
*are* ``/mcp/google-analytics``: a dedicated Analytics tool group, self-maintaining, with no
hand-written list of tools to fall out of step with the code (``app/core/mcp/sections.py``).
The six curated ``mcp_tools`` beside them are the in-app assistant's catalog, where a single
call beating four plus a judgement earns its place.

Every operation is a **read**. Not a phase: there is nothing in a client's Analytics property
this platform has any business writing, and an all-GET surface keeps answering past a licence
expiry, which is the right way round (§18).
"""

from __future__ import annotations

from app.integrations.google_analytics.mcp import GOOGLE_ANALYTICS_MCP_TOOLS
from app.integrations.google_analytics.permissions import GOOGLE_ANALYTICS_PERMISSIONS
from app.integrations.google_analytics.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="google_analytics",
    # A conversation with somebody else's service, not a capability of our own (§6a).
    kind=KIND_INTEGRATION,
    # The credential is a `google_connections` row; there is no second way to get one.
    requires=("google",),
    router=router,
    i18n_namespace="google_analytics",
    # Licensed (issue #137), the same bracket as `google` / `marketing` / `google_ads`. Nothing
    # here mutates, so the write gate governs only whether the integration may be newly enabled:
    # an install that already reads Analytics keeps reading it past an expiry, because data is
    # never hostage (epic #140).
    sku="google_analytics",
    permissions=GOOGLE_ANALYTICS_PERMISSIONS,
    # The in-app assistant's catalog. The external MCP surface is the router, one level up.
    mcp_tools=GOOGLE_ANALYTICS_MCP_TOOLS,
    # **No cron and no models.** Nothing is mirrored: every answer is fetched live under the
    # asking user's own grant, which is what makes this safe to ship with no migration and no
    # nightly quota cost. The nightly aggregate an agency wants for a *dashboard* already
    # exists, in `marketing`, and duplicating it here would be a second answer to a question
    # that has one.
)

registry.register(module)
