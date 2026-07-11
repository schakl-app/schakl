"""domains module (CLAUDE.md §6, issue #90/#92) — domains attached to a client.

Importing this package self-registers the module (router, company panel, permissions, i18n
namespace, and the daily public-DNS refresh cron) into the shared registry.
"""

from __future__ import annotations

from arq import cron

from app.modules.domains.jobs import refresh_all_domains
from app.modules.domains.panels import domains_company_panel
from app.modules.domains.permissions import DOMAIN_PERMISSIONS
from app.modules.domains.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="domains",
    router=router,
    i18n_namespace="domains",
    panels=[domains_company_panel],
    permissions=DOMAIN_PERMISSIONS,
    # Refresh every domain's nameservers + DNSSEC daily, off-peak and offset from other jobs (#92).
    cron_jobs=[cron(refresh_all_domains, hour=4, minute=30)],
)

registry.register(module)
