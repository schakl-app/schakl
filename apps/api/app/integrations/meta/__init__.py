"""meta — a client's Facebook Page and Instagram account as a first-class surface, and as MCP
tools. Business-licensed — see LICENSE. Architecture: ``docs/META.md``.

**What this is for.** An agency plans a client's social posts in one tool, approves them in a
chat, publishes them from a third, and answers "did Saturday's post go out" by opening
Instagram. Here a post is written once for the channels it goes to, stood behind by a named
person, published at its time — and every step is an API operation, which means every step is
an **MCP tool** (CLAUDE.md §12). An assistant asked to "prepare next month's posts for Nova
Fietsen" travels the same tenant resolution, the same permissions and the same company horizon
the browser does, and holds exactly the keys it was given: it can draft, and the schedule
route answers 403.

**Why an integration.** §6a's test: cancel the vendor and is the thing gone, or merely poorer?
Cancel Meta and there is nowhere to publish; every asset row is a pointer into somebody else's
state. The *posts* are ours — who wrote what for whom — which is why they are rows rather than
reads, but a planner with no channel is an empty screen.

**The account model is the design** (docs/META.md §1–§3). Meta gates who may *grant* a token,
not what the token may do, so the one route that needs no App Review is the agency's own app
in the agency's own Business portfolio, with a system user's token. There is therefore no
"connect your Facebook" button anywhere in this integration, and no instance-wide app.

**It requires nothing.** Not ``marketing`` (an agency may want the planner and no dashboard).
``meta_ads`` requires *this*.
"""

from __future__ import annotations

from arq import cron

from app.integrations.meta import provider
from app.integrations.meta.jobs import (
    meta_observe,
    meta_publish_due,
    meta_publish_post,
    meta_refresh_tokens,
)
from app.integrations.meta.panels import meta_company_panel
from app.integrations.meta.permissions import META_PERMISSIONS
from app.integrations.meta.router import router
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

provider.install()

module = ModuleDescriptor(
    name="meta",
    kind=KIND_INTEGRATION,
    router=router,
    i18n_namespace="meta",
    sku="meta",
    permissions=META_PERMISSIONS,
    panels=[meta_company_panel],
    cron_jobs=[
        # The clock. Once a minute, on the minute: a post scheduled for 09:00 goes out at
        # 09:00 and not at 09:14, and an org with nothing planned costs one indexed probe.
        cron(meta_publish_due, second=0),
        # 03:40 — before the working day in every zone an agency here lives in, and clear of
        # the Google jobs (04:45 onwards), which share the worker.
        cron(meta_refresh_tokens, hour=3, minute=40),
        cron(meta_observe, hour=3, minute=50),
    ],
    worker_functions=[meta_publish_post],
)

registry.register(module)
