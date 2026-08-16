"""mollie module (CLAUDE.md §6, epic #269 / issue #267) — the first payment provider.

Lets a tenant connect their own Mollie account and collect an invoice online: iDEAL, cards,
Bancontact, SEPA, PayPal — whatever their Mollie profile offers — through Mollie's own hosted
checkout, so schakl never touches card data and holds no PCI scope at all.

What this module owns is deliberately narrow: **a credential and a conversation**. Where a
payment goes, what it settles, whether an invoice is now paid — all of that is
``invoicing``'s, reached through the provider seam in ``app.core.payments`` and never by
importing either module into the other (§6). That split is what makes a second provider a new
package rather than a refactor, which is the one place this deviates from issue #267: it argued
against an abstraction on the grounds that no second provider was on the roadmap, and the owner
asked for one so that Stripe and Adyen stay a file away. ``docs/PAYMENTS.md`` records the
reversal and its reasoning; ``docs/MOLLIE.md`` is this half.

**Written from Mollie's official API reference; not yet exercised against a live account.**
See ``docs/MOLLIE.md`` §1 for what that means for the parsing in ``client.py`` and exactly what
to check the day a credential arrives.

Importing this package self-registers the module, its payment provider and its account
resolver.
"""

from __future__ import annotations

from app.core.payments import register_payment_accounts, register_payment_provider
from app.integrations.mollie.client import MolliePaymentProvider
from app.integrations.mollie.permissions import MOLLIE_PERMISSIONS
from app.integrations.mollie.router import router
from app.integrations.mollie.service import resolve_accounts
from app.registry import KIND_INTEGRATION, ModuleDescriptor, registry

module = ModuleDescriptor(
    name="mollie",
    # A conversation with somebody else's service, not a capability of our own.
    kind=KIND_INTEGRATION,
    # Requires `invoicing`: it contributes no nav item and no panel, and the routes it is reached
    # through are invoicing's own (`/invoicing/invoices/{id}/payment-intents`). What it settles is
    # an `InvoicePayment`. With `invoicing` off there is no document to collect against.
    requires=("invoicing",),
    router=router,
    i18n_namespace="mollie",
    # Licensed module (issue #137): a paid integration, the same bracket as ``invoicing`` /
    # ``oxxa`` / ``google``, never part of the free CRM core (epic #140). Past expiry+grace
    # the mount-time gate turns every mutation 402, so no *new* credential can be connected
    # and no key rotated — while the read surface keeps working. Note what is deliberately
    # **not** gated: the callback that records a payment a client has already made lives on
    # invoicing's router and carries ``license_exempt``. An expired licence makes a module
    # read-only; it does not make the agency's takings disappear.
    sku="mollie",
    permissions=MOLLIE_PERMISSIONS,
)

registry.register(module)

# The payment seam (#267 scope items 1–3). Registering the **class** rather than an instance: a
# provider is constructed per credential, and there is one credential per account row.
register_payment_provider(MolliePaymentProvider.key, MolliePaymentProvider)
# …and how to find those credentials, without core or invoicing learning this module's table.
register_payment_accounts(MolliePaymentProvider.key, resolve_accounts)
