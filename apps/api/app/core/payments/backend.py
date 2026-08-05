"""The ``PaymentProvider`` protocol and its dispatch (epic #269, issue #267).

Core owns the vocabulary; a module (``mollie``, and whatever comes after it) owns the protocol
talking to one. The precedent is :mod:`app.core.registrar` — protocol + registry + config-free
dispatch — and the reason it lives here rather than inside ``invoicing`` is §6: the module that
*collects* the money may not import the module that *asks* for it, in either direction.

Issue #267 argued for no abstraction at all, on the grounds that Mollie already aggregates the
methods an NL/EU agency needs and a second provider was hypothetical. That was true about
*methods* and wrong about *providers*: Stripe and Adyen are ordinary asks from an agency with
non-EU clients, and the shape below costs one file today against a refactor of the settle path
later — the same trade the registrar seam made in #296 and the accounting seam made in #31.
What is *not* abstracted is the ledger: a confirmed payment writes an ordinary
``InvoicePayment`` row, so invoicing stays the single answer to "what has been paid".

Four rules the shape encodes, three of them learned from Mollie's own security model:

* **A webhook is a hint, never a fact.** :meth:`PaymentProvider.references_in_webhook` extracts
  ids from an *unauthenticated* body; nothing may act on it. The authority is
  :meth:`PaymentProvider.fetch_payment`, an authenticated call to the provider with the
  tenant's own credential. Mollie states this explicitly and posts no status at all; Stripe and
  Adyen post the whole event, which is exactly why a seam that trusted the body would be
  written to Stripe's shape and unsafe for Mollie's.
* **Signature verification is an extra gate, never the gate.** :meth:`verify_webhook` defaults
  to accepting: Mollie's per-payment ``webhookUrl`` contract has no signature. A provider that
  does sign (Stripe's ``Stripe-Signature``, Adyen's HMAC, Mollie's own next-gen webhooks)
  overrides it, and it runs *after* the credential is resolved because the secret is per-tenant.
* **Everything a provider returns is an observation.** :class:`PaymentSnapshot` is what the
  provider said. Deciding what that means for an invoice — and storing the decision beside the
  observation so "we never settled this" is expressible (CLAUDE.md §10) — is the caller's job,
  in its own tables.
* **Money is a ``Decimal`` here and a string on the wire.** Providers overwhelmingly serialise
  amounts as decimal strings precisely to keep floats out; the conversion happens in the
  adapter, and a float never appears on either side of it.

There is deliberately **no ``refund``**. A refund moves money in the other direction and is not
reversible; this seam exists for the collect-and-reconcile slice, and an issue that wants
refunds from schakl should extend it consciously rather than inherit the power by accident.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, ClassVar, Protocol, runtime_checkable


class PaymentStatus(StrEnum):
    """The states every provider's lifecycle is normalised onto.

    Mollie's own vocabulary, because it is the superset of what the others report and every
    value here is a state a human reading an invoice needs told apart. ``AUTHORIZED`` is
    deliberately **not** ``PAID``: the money is held, not captured, and booking it as received
    would credit an invoice against funds that can still be released.
    """

    OPEN = "open"
    PENDING = "pending"
    AUTHORIZED = "authorized"
    PAID = "paid"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELED = "canceled"

    @property
    def settled(self) -> bool:
        """Money has actually arrived. The only status that may write a payment row."""
        return self is PaymentStatus.PAID

    @property
    def final(self) -> bool:
        """Nothing more will happen to this payment — stop polling it."""
        return self in _FINAL


_FINAL = frozenset(
    {
        PaymentStatus.PAID,
        PaymentStatus.FAILED,
        PaymentStatus.EXPIRED,
        PaymentStatus.CANCELED,
    }
)


class PaymentProviderError(RuntimeError):
    """A provider call failed.

    ``message`` is the **provider's own text** and is therefore untranslatable: it belongs on a
    row's ``last_error`` where a human can read it, never in the error envelope, whose
    ``message`` is an i18n key (CLAUDE.md §9). ``code`` is the provider's own token where it
    gave one; ``http_status`` its HTTP status where the failure had one.
    """

    def __init__(
        self, message: str, *, code: str | None = None, http_status: int | None = None
    ) -> None:
        super().__init__(message)
        self.code = code
        self.http_status = http_status


class PaymentProviderAuthError(PaymentProviderError):
    """The credential was rejected. Its own class because retrying cannot help — only the
    tenant can fix it, by re-entering the credential."""


@dataclass(frozen=True)
class PaymentRequest:
    """What schakl asks a provider to collect. Provider-independent by construction.

    ``reference`` is **our** id for the thing being paid (the intent's uuid). It goes out as
    provider metadata so a human staring at the provider's dashboard can find the invoice, and
    it comes back on the snapshot — but it is never how a webhook is resolved: an id we chose
    is not evidence, and the mapping lives in our own table.
    """

    amount: Decimal
    currency: str
    #: Shown on the payer's bank or card statement. Providers truncate; keep it short and
    #: recognisable ("Factuur 2026-0041").
    description: str
    #: Where the payer lands when the provider is done, whatever the outcome.
    return_url: str
    #: Where the provider posts status changes. Must be internet-reachable.
    webhook_url: str
    reference: str
    #: ``xx_XX``. The provider shows its checkout in this language where it supports it.
    locale: str | None = None
    #: Where the payer lands when they *explicitly* cancel, if the provider distinguishes it.
    cancel_url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentSnapshot:
    """One payment as the provider last described it.

    Every field except ``reference`` and ``status`` is optional on purpose: providers answer
    different subsets at different points in the lifecycle, and a partial answer must be
    storable. ``None`` means *not reported*, which is never the same as zero or false.
    """

    #: The provider's own id (Mollie's ``tr_…``). Unique per provider, and the key a webhook
    #: is resolved by.
    reference: str
    status: PaymentStatus
    amount: Decimal | None = None
    currency: str | None = None
    #: The method the payer actually chose, in the provider's vocabulary (``ideal``,
    #: ``creditcard``). ``None`` until they pick one.
    method: str | None = None
    #: ``live`` or ``test``. Stored so a test payment can never be mistaken for revenue.
    mode: str | None = None
    paid_at: datetime | None = None
    #: Where to send the payer. Present on a fresh payment, gone once it is no longer payable.
    checkout_url: str | None = None
    #: The provider's own failure/cancellation reason, where it gave one. Untranslatable text.
    detail: str | None = None
    #: The parsed response, for the trail. Must not contain the credential.
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class PaymentProvider(Protocol):
    """What schakl may ask a payment provider. One instance per credential, cheap to build."""

    #: Stable slug, unique across providers (``"mollie"``). Matches the owning module's name.
    key: ClassVar[str]

    async def verify(self) -> dict[str, Any]:
        """Prove the credential works. Returns a small provider-specific fact dict for display
        (the profile name, the enabled methods, the mode). Raises
        :class:`PaymentProviderAuthError` when rejected."""
        ...

    async def create_payment(self, request: PaymentRequest) -> PaymentSnapshot:
        """Open a payment and return it, including the ``checkout_url`` to send the payer to.

        **Not idempotent, and must not be retried blind.** A duplicate here is a second
        checkout link for one invoice; the caller sends an idempotency key where the provider
        supports one and treats a timeout as *unknown* rather than as failure (#31's rule).
        """
        ...

    async def fetch_payment(self, reference: str) -> PaymentSnapshot | None:
        """The provider's current truth about one payment. ``None`` when it does not know the
        reference — a webhook naming a payment this credential never created is not an error,
        it is somebody else's payment or a forgery, and both are answered the same way."""
        ...

    @classmethod
    def references_in_webhook(cls, body: bytes, headers: Mapping[str, str]) -> list[str]:
        """Provider payment ids named by an **unauthenticated** callback body.

        A classmethod because it runs before any credential is resolved — the tenant is not
        known yet, and this is how it becomes known. Returns ``[]`` for anything unparseable;
        a malformed body is not an exception, it is noise on a public endpoint.
        """
        ...

    def verify_webhook(self, body: bytes, headers: Mapping[str, str]) -> bool:
        """Optional second gate, once the tenant's credential is in hand.

        Return ``True`` when the provider offers no signature (Mollie's per-payment webhook
        contract does not) — the authenticated re-fetch is what makes that safe. A provider
        that signs compares with :func:`hmac.compare_digest` and returns ``False`` on a
        mismatch, and the caller answers 404 without touching any state.
        """
        ...


#: Provider classes by key. Populated by each module at import time, the same self-registration
#: ``ModuleDescriptor`` uses — core names no provider (CLAUDE.md §6).
_PROVIDERS: dict[str, type] = {}


def register_payment_provider(key: str, factory: type) -> None:
    """Register a provider class under ``key``. Re-registering the same key is a programming
    error, not a silent replacement — two providers answering to one slug is unfixable at
    runtime, and here it would mean a webhook parsed by the wrong adapter."""
    existing = _PROVIDERS.get(key)
    if existing is not None and existing is not factory:
        raise ValueError(f"payment provider {key!r} is already registered")
    _PROVIDERS[key] = factory


def get_payment_provider(key: str) -> type:
    """The provider class for ``key``. Raises :class:`LookupError` when its module is disabled —
    the caller turns that into a 400/404, never a 500."""
    try:
        return _PROVIDERS[key]
    except KeyError as exc:
        raise LookupError(f"no payment provider registered for {key!r}") from exc


def known_payment_providers() -> tuple[str, ...]:
    """Registered keys, sorted. Only the *enabled* modules appear — which is what makes this
    safe to render as a picker."""
    return tuple(sorted(_PROVIDERS))
