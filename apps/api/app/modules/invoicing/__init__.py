"""invoicing module (CLAUDE.md §6, issue #207) — native invoices & quotes.

Importing this package self-registers the module (router, company panel, permissions, i18n
namespace, the daily reminders/expiry cron) and subscribes the ``subscription.due`` consumer
that turns #30's cycle events into draft invoices.
"""

from __future__ import annotations

from arq import cron

from app.core.events import subscribe
from app.modules.invoicing.events import on_subscription_due
from app.modules.invoicing.jobs import invoicing_daily
from app.modules.invoicing.panels import invoicing_company_panel
from app.modules.invoicing.permissions import INVOICING_PERMISSIONS
from app.modules.invoicing.router import router
from app.registry import ModuleDescriptor, registry

module = ModuleDescriptor(
    name="invoicing",
    router=router,
    i18n_namespace="invoicing",
    # Licensed like subscriptions (#137): the billing suite is premium; past expiry+grace it
    # goes read-only (mutations 402) — reads, prints and exports stay.
    sku="invoicing",
    panels=[invoicing_company_panel],
    permissions=INVOICING_PERMISSIONS,
    # Daily, after the subscriptions cycle (05:30) has drafted its invoices: payment
    # reminders + quote expiry (#207).
    cron_jobs=[cron(invoicing_daily, hour=6, minute=15)],
)

registry.register(module)

# The subscriptions module deliberately raises no invoices (#30); this is the consumer it
# emits ``subscription.due`` for. Subscribed at import, like every cross-module reaction.
subscribe("subscription.due", on_subscription_due)
