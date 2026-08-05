"""invoicing module (CLAUDE.md §6, issue #207) — native invoices & quotes.

Importing this package self-registers the module (router, company panel, permissions, i18n
namespace, the customisable client mails, the daily reminders/expiry cron) and subscribes the
``subscription.due`` consumer that turns #30's cycle events into draft invoices.
"""

from __future__ import annotations

from arq import cron

from app.core.events import subscribe
from app.modules.invoicing.bulk import INVOICE_BULK
from app.modules.invoicing.emails import INVOICING_EMAIL_KINDS
from app.modules.invoicing.events import on_domain_due, on_subscription_due
from app.modules.invoicing.jobs import invoicing_daily, invoicing_payments_reconcile
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
    bulk=[INVOICE_BULK],
    # The three mails a client reads (invoice, quote, reminder), rewritable per locale in
    # Instellingen -> E-mail like the auth mails already were (#161 tier 2, §6).
    email_templates=INVOICING_EMAIL_KINDS,
    cron_jobs=[
        # Daily, after the subscriptions cycle (05:30) has drafted its invoices: payment
        # reminders + quote expiry (#207).
        cron(invoicing_daily, hour=6, minute=15),
        # **Hourly**, not daily (epic #269): the safety net under the payment callback. A
        # webhook can be lost for entirely ordinary reasons — an access proxy in front of the
        # API, a redeploy, a firewall rule — and the failure is invisible from outside: the
        # client's money moved and the invoice still says open. Cheap when nothing is in
        # flight (one query per org, none at all for an org with no provider connected), and
        # an agency chasing "the client says they paid" should not have to wait out a nightly
        # job. Off the hour so it does not pile onto everything else scheduled at :00.
        cron(invoicing_payments_reconcile, minute=25),
    ],
)

registry.register(module)

# The subscriptions module deliberately raises no invoices (#30); this is the consumer it
# emits ``subscription.due`` for. Subscribed at import, like every cross-module reaction.
subscribe("subscription.due", on_subscription_due)
# Domain renewals (#250) follow the same seam: the domains cron owns the cycle, this drafts.
subscribe("domain.due", on_domain_due)
