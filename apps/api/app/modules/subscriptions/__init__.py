"""subscriptions module (CLAUDE.md §6, issue #30) — recurring client agreements.

Importing this package self-registers the module (router, company panel, permissions, i18n
namespace, and the daily invoice-cycle cron) into the shared registry.
"""

from __future__ import annotations

from arq import cron

from app.modules.subscriptions.impex import SUBSCRIPTION_IMPEX
from app.modules.subscriptions.jobs import advance_subscriptions
from app.modules.subscriptions.panels import subscriptions_company_panel
from app.modules.subscriptions.permissions import SUBSCRIPTION_PERMISSIONS
from app.modules.subscriptions.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="subscriptions",
    router=router,
    i18n_namespace="subscriptions",
    panels=[subscriptions_company_panel],
    permissions=SUBSCRIPTION_PERMISSIONS,
    impex=[SUBSCRIPTION_IMPEX],
    # Daily, early org-morning: fire subscription.due and advance the cycle (#30).
    cron_jobs=[cron(advance_subscriptions, hour=5, minute=30)],
)

registry.register(module)
