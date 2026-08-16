"""snelstart integration (CLAUDE.md §6, epic #377, issue #31) — the first accounting provider.

Lets a Dutch agency connect their own SnelStart administration so clients, invoices and articles
stop being typed twice, and so *"who hasn't paid"* is answerable in the CRM — which is the
question SnelStart is authoritative about and the reason most agencies want this at all.

What this module owns is narrow and stated the same way ``mollie``'s is: **a credential and a
conversation**. What an invoice *is*, what has been paid, which client owes what — all of that is
``invoicing``'s, and nothing here keeps a second copy of it (#31: the CRM is not the ledger). The
one thing that flows back becomes an ordinary ``InvoicePayment``, so every downstream behaviour —
settling, ``invoice.paid``, dunning, the client portal — cannot tell it from a payment somebody
typed in, because functionally that is what it is.

**Written against the live B2B-API v2 with a working koppelsleutel**, not from memory (§11).
``docs/SNELSTART.md`` §1 lists every call that was actually made, what it answered, and the two
behaviours the documentation does not mention — ``$filter`` being silently ignored by some
endpoints, and there being no paging metadata at all.

Importing this package self-registers the module, its accounting provider and its company panel.
"""

from __future__ import annotations

from arq import cron

from app.integrations.snelstart.jobs import snelstart_nightly, snelstart_prune_runs
from app.integrations.snelstart.panels import SNELSTART_PANELS
from app.integrations.snelstart.permissions import SNELSTART_PERMISSIONS
from app.integrations.snelstart.provider import SnelstartAccountingProvider
from app.integrations.snelstart.router import router
from app.modules.invoicing.accounting import register_provider
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="snelstart",
    # A conversation with somebody else's service, not a capability of our own.
    kind=KIND_INTEGRATION,
    # Requires `invoicing`: what it pushes is an invoice, what it pulls is a payment against
    # one, and the seam it fills (`invoicing.accounting.AccountingProvider`) lives there. With
    # `invoicing` off there is no document to book and nothing to reconcile.
    requires=("invoicing",),
    router=router,
    i18n_namespace="snelstart",
    # Licensed, the same bracket as `invoicing` / `mollie` / `oxxa` (epic #140). Past
    # expiry+grace the mount-time gate turns every mutation 402, so no new administration can be
    # connected and nothing new is pushed — while reads keep working, which is what an agency
    # needs to see what *was* pushed. The one exemption is the coupling callback: a koppelsleutel
    # arrives once and SnelStart never retries, so a 402 there would drop a credential the
    # tenant has already approved with no mechanism that would ever deliver it again.
    sku="snelstart",
    permissions=SNELSTART_PERMISSIONS,
    panels=SNELSTART_PANELS,
    cron_jobs=[
        # 04:40 — clear of the platform's 04:00/05:00/05:30 jobs, and after midnight in every
        # European zone so "yesterday's payments" means yesterday.
        cron(snelstart_nightly, hour=4, minute=40),
        cron(snelstart_prune_runs, hour=3, minute=40, weekday=0),
    ],
)

registry.register(module)

# The accounting seam #31 asked for and #207 shipped empty. Registering an instance rather than
# a class, unlike the payment seam: an accounting provider holds no credential of its own — it
# resolves the tenant's connected administration per call — so one instance serves every org.
register_provider(SnelstartAccountingProvider())
