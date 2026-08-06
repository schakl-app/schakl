"""Payment-provider seam (epic #269, issue #267) — what schakl may ask *any* payment provider.

Core owns the vocabulary and the callback addressing; a module (``mollie``, and Stripe or Adyen
after it) owns the protocol talking to one; ``invoicing`` owns what a confirmed payment *means*
and writes it to the ledger it already had. Three layers, none of which imports another
module's internals (CLAUDE.md §6).

Read :mod:`app.core.payments.backend` for the protocol and why a webhook body is never trusted,
:mod:`app.core.payments.accounts` for how a credential is found without naming a provider, and
:mod:`app.core.payments.tokens` for how an unauthenticated callback names its tenant.
``docs/PAYMENTS.md`` is the architecture; ``docs/MOLLIE.md`` the first implementation.
"""

from __future__ import annotations

from app.core.payments.accounts import (
    AccountResolver,
    PaymentAccount,
    available_accounts,
    register_payment_accounts,
    resolve_account,
)
from app.core.payments.backend import (
    PaymentProvider,
    PaymentProviderAuthError,
    PaymentProviderError,
    PaymentRequest,
    PaymentSnapshot,
    PaymentStatus,
    get_payment_provider,
    known_payment_providers,
    register_payment_provider,
)

__all__ = [
    "AccountResolver",
    "PaymentAccount",
    "PaymentProvider",
    "PaymentProviderAuthError",
    "PaymentProviderError",
    "PaymentRequest",
    "PaymentSnapshot",
    "PaymentStatus",
    "available_accounts",
    "get_payment_provider",
    "known_payment_providers",
    "register_payment_accounts",
    "register_payment_provider",
    "resolve_account",
]
