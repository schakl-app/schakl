"""The accounting-provider seam (#31, shipped with #207 ahead of the first live provider).

The design #31 asked for, provider-independent parts first:

- **`ExternalRef`** (models.py) + ``ExternalRefService`` (service.py): what a bookkeeping
  package knows about a local record, keyed ``(provider, local_type, local_id)`` — the
  idempotency that makes "never create the same invoice twice" structural.
- **`AccountingProvider`**: the contract a live adapter (Exact Online, SnelStart,
  Moneybird, e-Boekhouden) implements. Adapters self-register at import; the router only
  ever talks to the registry, so adding a provider is a new module file, not an edit here.
- **UBL** (ubl.py) is the provider-less bridge available today: both Exact Online and
  SnelStart import UBL 2.1 sales invoices, so an agency connects its books the day this
  ships — by file, with the OAuth sync as the later upgrade behind this same seam.

An adapter's ``export_invoice`` receives the invoice with lines/totals attached plus the
seller block, performs its remote work, and returns the remote id; the caller records the
``ExternalRef``. Adapters must treat a timeout as *unknown* — look the document up before
retrying a create (#31) — and must never receive the caller's MCP/API credential (§12).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.tenancy import RequestContext


@dataclass(frozen=True)
class ExportResult:
    external_id: str
    payload: dict[str, Any]


class AccountingProvider(Protocol):
    """One accounting package. ``key`` is stored in ``external_refs.provider``."""

    key: str
    #: Human name for the Boekhouding settings screen ("Exact Online", "SnelStart").
    label: str

    async def export_invoice(
        self,
        ctx: RequestContext,
        invoice: Any,
        seller: dict[str, Any],
    ) -> ExportResult:
        """Create/update the sales invoice remotely and return its remote identity.
        Must be idempotent per invoice — check for an existing remote document first."""
        ...


_PROVIDERS: dict[str, AccountingProvider] = {}


def register_provider(provider: AccountingProvider) -> AccountingProvider:
    if provider.key in _PROVIDERS:
        raise ValueError(f"accounting provider '{provider.key}' already registered")
    _PROVIDERS[provider.key] = provider
    return provider


def get_provider(key: str) -> AccountingProvider | None:
    return _PROVIDERS.get(key)


def available_providers() -> list[AccountingProvider]:
    return list(_PROVIDERS.values())
