"""marketing module (CLAUDE.md §6, epic #134) — GA4, Search Console & Google Ads per client.

Links each client (company) to the Google marketing properties it owns (#132), syncs small
daily aggregates for every link (#133), and surfaces them where a marketeer works: a KPI panel
on the client, a marketing tab per client, and one cross-client overview.

It **rides the ``google`` core** (issue #22): the OAuth connect flow, the encrypted token vault
and the "act as user X" client factory are all the google module's; this module only *adds
scopes* (``analytics.readonly``, ``webmasters.readonly``, ``adwords``) via incremental
authorization and calls the APIs through ``google.client.acting_as``. It builds no second OAuth.

``source`` is an enum on the link table, not three hardcoded columns — a fourth source later
(Meta, LinkedIn) is a new enum value + adapter, not a schema redesign. **Google Tag Manager is
deliberately not a source**: GTM is a tag-deployment tool with no marketeer-facing metrics of
its own (the conversions it fires already surface through GA4), so a container link would add a
scope and a picker for zero data in a client-overview CRM.

Importing this package self-registers the module.
"""

from __future__ import annotations

from arq import cron

from app.modules.marketing.jobs import (
    marketing_backfill_link,
    marketing_sync_all,
    marketing_sync_link,
)
from app.modules.marketing.panels import marketing_company_panel
from app.modules.marketing.permissions import MARKETING_PERMISSIONS
from app.modules.marketing.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="marketing",
    router=router,
    i18n_namespace="marketing",
    # Licensed module (issue #137): the per-client GA4/GSC/Ads integration is a premium
    # capability, its own commercial boundary — a distinct sku from the ``google`` core it rides
    # (a tenant licenses Google Workspace and marketing analytics separately). Past
    # expiry+grace it goes read-only: linking/unlinking + settings turn 402 at the mount-time
    # gate and the nightly sync crons stand down (they write on a schedule, so the route gate
    # alone would not stop them); the panel/tab/overview keep reading the already-synced data.
    sku="marketing",
    panels=[marketing_company_panel],
    permissions=MARKETING_PERMISSIONS,
    # Nightly, per org, after the platform's other early jobs (subscriptions runs 05:30). The
    # daily aggregates power the panel/tab/overview without burning Google quota on page views.
    cron_jobs=[cron(marketing_sync_all, hour=4, minute=45)],
    # One-off jobs the API/cron enqueue by name: per-link nightly sync and the 13-month backfill
    # kicked off when a link is first created. Names are globally unique.
    worker_functions=[marketing_sync_link, marketing_backfill_link],
)

registry.register(module)
