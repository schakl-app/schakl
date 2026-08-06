"""How a tenant's payment credential is found without core (or invoicing) naming a provider.

:mod:`app.core.payments.backend` says what a provider can *do*; this says which of them this
org has actually connected, and hands back the one fact the caller cannot derive: a live
client built from a credential only the owning module may decrypt.

The shape is ``app.core.registrar.presence`` applied to a row instead of a predicate — a
module registers one async resolver for its own table, core only composes. That is what keeps
``invoicing`` able to ask *"what can this org charge with?"* while remaining unable to read
``mollie_accounts``, and what makes a second provider a new module rather than an edit here.

Two properties are load-bearing:

* **The resolver returns the webhook secret, and core verifies it.** The callback URL carries
  ``{org_id}.{account_id}.{secret}`` (the Google Calendar channel-token pattern, docs/GOOGLE.md)
  because a payment provider posts no tenant hostname and no session. Resolution therefore has
  to happen *before* anything is scoped, so the secret has to travel to the verifier rather
  than the verification travelling into the module.
* **Connecting is lazy.** ``connect`` is a zero-argument callable, so listing the accounts for
  a picker never decrypts a credential, and a decryption failure surfaces at the one call site
  that can explain it (a rotated ``SCHAKL_ENCRYPTION_KEY`` means re-enter the key, not retry).
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # pragma: no cover — typing only
    from app.core.payments.backend import PaymentProvider


@dataclass(frozen=True)
class PaymentAccount:
    """One connected payment credential, described in terms core is allowed to know."""

    provider: str
    id: uuid.UUID
    org_id: uuid.UUID
    #: The tenant's own name for it ("Mollie — Breik"), shown in a picker. Not i18n'd: it names
    #: a thing the tenant owns, like ``providers.name``.
    label: str
    #: ``live`` or ``test``, as the credential itself declares. Rendered beside the label so
    #: nobody takes real money while testing — or, worse, believes they did.
    mode: str
    #: Whether the tenant has this account switched on. An inactive account is still resolvable
    #: (a webhook for a payment it created must still settle), it just cannot start new ones.
    active: bool
    #: The secret half of the callback token. Never leaves the server, never reaches a schema.
    webhook_secret: str
    #: Builds the live client. Raises whatever the owning module raises when the stored
    #: credential cannot be read.
    connect: Callable[[], PaymentProvider]


#: ``(session, org_id) -> [PaymentAccount]`` per provider key, registered by its module.
AccountResolver = Callable[[AsyncSession, uuid.UUID], Awaitable[list[PaymentAccount]]]

_RESOLVERS: dict[str, AccountResolver] = {}


def register_payment_accounts(key: str, resolver: AccountResolver) -> None:
    """Register ``key``'s account resolver. Duplicate registration is a programming error for
    the same reason it is in the provider registry: the second one would silently shadow."""
    existing = _RESOLVERS.get(key)
    if existing is not None and existing is not resolver:
        raise ValueError(f"payment accounts for {key!r} are already registered")
    _RESOLVERS[key] = resolver


async def available_accounts(
    session: AsyncSession, org_id: uuid.UUID, *, provider: str | None = None
) -> list[PaymentAccount]:
    """Every payment credential this org has connected, across the enabled modules.

    Ordered by provider then label so a picker is stable. The caller decides what to do with
    an ``active=False`` row — the list is the honest answer to "what is connected", and hiding
    a disabled account would make "why can I not pay?" unanswerable from the screen.
    """
    keys = [provider] if provider is not None else sorted(_RESOLVERS)
    accounts: list[PaymentAccount] = []
    for key in keys:
        resolver = _RESOLVERS.get(key)
        if resolver is None:
            continue
        accounts.extend(await resolver(session, org_id))
    return sorted(accounts, key=lambda a: (a.provider, a.label.lower()))


async def resolve_account(
    session: AsyncSession, org_id: uuid.UUID, provider: str, account_id: uuid.UUID
) -> PaymentAccount | None:
    """One specific credential, or ``None`` when this org does not hold it.

    The RLS GUC must already be bound to ``org_id`` — every resolver reads an org-scoped,
    RLS-forced table, so an unbound session fails closed and this answers ``None``, which is
    the right answer to give a caller who has not proven which tenant they are.
    """
    for account in await available_accounts(session, org_id, provider=provider):
        if account.id == account_id:
            return account
    return None
