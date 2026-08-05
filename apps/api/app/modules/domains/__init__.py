"""domains module (CLAUDE.md §6, issue #90/#92) — domains attached to a client.

Importing this package self-registers the module (router, company panel, permissions, i18n
namespace, and the daily public-DNS refresh cron) into the shared registry.
"""

from __future__ import annotations

from arq import cron

from app.modules.domains.impex import DOMAIN_IMPEX, TLD_PRICE_IMPEX
from app.modules.domains.jobs import (
    advance_domain_renewals,
    refresh_all_domains,
    refresh_domain_dns,
)
from app.modules.domains.panels import domains_company_panel
from app.modules.domains.permissions import DOMAIN_PERMISSIONS
from app.modules.domains.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="domains",
    router=router,
    i18n_namespace="domains",
    # Licensed module (issue #137). Its own sku — the web-assets trio (domains, websites,
    # hosting) is bundled in *license documents* (a plan lists all three skus), never in code.
    sku="domains",
    panels=[domains_company_panel],
    permissions=DOMAIN_PERMISSIONS,
    impex=[DOMAIN_IMPEX, TLD_PRICE_IMPEX],
    # Refresh every domain's nameservers + DNSSEC + MX daily, off-peak and offset from other
    # jobs (#92, #125). The renewal cycle (#250) fires after the subscriptions cycle (05:30)
    # and before invoicing's daily (06:15), so drafts exist when reminders/summaries look.
    cron_jobs=[
        cron(refresh_all_domains, hour=4, minute=30),
        cron(advance_domain_renewals, hour=5, minute=45),
    ],
    # One-off first fetch right after a domain is created (#125).
    worker_functions=[refresh_domain_dns],
)

registry.register(module)
