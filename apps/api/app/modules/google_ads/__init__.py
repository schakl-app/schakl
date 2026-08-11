"""google_ads module — Google Ads as a first-class surface, and as MCP tools.

Business-licensed — see LICENSE.

**What this is for.** An agency's Google Ads work is answering questions ("what did July cost",
"which search terms burned money without converting", "does this account still optimise toward a
real conversion") and then acting on the answers. Both halves are now API operations, which means
both halves are **MCP tools**: every ``/api/v1`` route becomes one, generated from this app's own
OpenAPI document and proxied in-process back through ``require_context`` (CLAUDE.md §12). An
assistant asking about a client's spend travels the same tenant resolution, the same RLS binding
and the same permission checks as the browser does, and can never exceed the API key's scopes.

**Why a module rather than more depth in ``marketing``.** Three things differ. It is separately
licensed (``sku="google_ads"``); it *writes* to somebody else's live advertising account, which
needs its own gates and its own kill switch; and it owns a fact ``marketing`` was about to own a
second copy of — which Ads customer a client is. That last one is why
:mod:`app.core.googleads` exists: the transport and the "which account" protocol sit in core
where both modules reach them, because §6 forbids either importing the other.

**Where it sits.** Under Marketing in the menu, as its sibling: the dashboard answers *how the
client is doing*, this answers *what the advertising is doing and what to change*.
"""

from __future__ import annotations

from arq import cron

from app.modules.google_ads.jobs import google_ads_backfill_account, google_ads_sync_all
from app.modules.google_ads.mcp import GOOGLE_ADS_MCP_TOOLS
from app.modules.google_ads.panels import google_ads_company_panel
from app.modules.google_ads.permissions import GOOGLE_ADS_PERMISSIONS
from app.modules.google_ads.provider import install as install_provider
from app.modules.google_ads.report_sections import GOOGLE_ADS_REPORT_SECTIONS
from app.modules.google_ads.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="google_ads",
    router=router,
    i18n_namespace="google_ads",
    # Licensed (issue #137), the same bracket as ``google`` / ``marketing`` / ``cloudflare``.
    # Past expiry+grace the mount-time gate turns every mutation 402 while the reads keep
    # working: an expired licence must never leave an agency unable to look up what a client's
    # campaigns did last month, and it certainly must not leave a campaign half-edited.
    sku="google_ads",
    permissions=GOOGLE_ADS_PERMISSIONS,
    panels=[google_ads_company_panel],
    # Curated tools *beside* the ones every route already contributes: the three shapes where a
    # single call beats three plus arithmetic the model should not be doing.
    mcp_tools=GOOGLE_ADS_MCP_TOOLS,
    # 05:15, deliberately after marketing's 04:45: both walk every org making outbound Google
    # calls, and stacking them on one minute is how a box with thirty clients meets its own rate
    # limits at four in the morning.
    cron_jobs=[cron(google_ads_sync_all, hour=5, minute=15)],
    worker_functions=[google_ads_backfill_account],
    # The Ads half of the monthly client report (#300): the panels pattern, applied to
    # documents. Both sections read the nightly mirror, which is what makes a report of last
    # March still printable next March.
    report_sections=GOOGLE_ADS_REPORT_SECTIONS,
)

# The core seam's provider. Registered at import — the same shape ``cloudflare`` uses for
# ``register_presence`` — so that ``marketing`` can resolve an Ads account without ever naming
# this module, and gets a clean "not configured" when the module is absent instead of an
# ImportError that would take the API and the worker down with it.
install_provider()

registry.register(module)
