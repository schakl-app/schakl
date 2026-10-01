"""meta_ads — a client's Meta advertising as a first-class surface, and as MCP tools.
Business-licensed — see LICENSE. Architecture: ``docs/META.md``.

**What this is for.** Reading what a client's campaigns cost and returned, and changing them:
building a campaign, setting its budget, switching it on — each an API operation, which means
each an **MCP tool** (CLAUDE.md §12), travelling the same tenant resolution, permissions and
company horizon the browser does.

**What keeps that safe is structure, not caution.** Everything is *created paused*, because
no create reads a status from its caller. Switching something on is a separate route with a
separate permission, so a key can be minted that may build a whole campaign and cannot spend
a cent. A budget is judged against the agency's guardrails before it is sent, and every
change is written down with who made it and why.

**It requires ``meta``** and nothing else: the ad account is a ``meta_assets`` row and the
credential is that integration's. It holds no token of its own.

**It mirrors nothing.** Campaigns, ad sets and ads are read from Meta when asked for. The
only rows here are the ones Meta has no place for: the guardrails, and the decisions.
"""

from __future__ import annotations

from app.integrations.meta_ads.panels import meta_ads_company_panel
from app.integrations.meta_ads.permissions import META_ADS_PERMISSIONS
from app.integrations.meta_ads.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="meta_ads",
    kind=KIND_INTEGRATION,
    requires=("meta",),
    router=router,
    i18n_namespace="meta_ads",
    sku="meta_ads",
    permissions=META_ADS_PERMISSIONS,
    panels=[meta_ads_company_panel],
)

registry.register(module)
