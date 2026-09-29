"""Which Meta assets are this client's, and what it takes to call one.

``meta`` owns the rows and registers the provider; ``marketing`` (and anything else that is not
allowed to import an integration's internals, §6) asks here. The shape is
``app.core.googleads.accounts``: a protocol in core, a registration from whoever holds the data,
and a default that answers honestly when nobody registered.

**Absence raises rather than returning ``None``** for the reason stated there: a ``None``
account id reaches the URL builder and asks Meta about an ad account called ``act_None``.

**The provider does not re-check permissions** (``google_ads``' reasoning): a borrower is
already inside its own gated route, and the two rules that protect the rows travel with the
query — RLS binds the org, and every read goes through the repository, which carries the
company horizon. A second module's permission on top would silently empty a dashboard tile
for every member nobody thought to grant it to.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.metagraph.client import MetaCredentials
from app.core.metagraph.errors import MetaNotConfigured

KIND_PAGE = "page"
KIND_INSTAGRAM = "instagram"
KIND_AD_ACCOUNT = "ad_account"


@dataclass(frozen=True)
class MetaAssetRef:
    """One linked asset, as a borrower is allowed to see it."""

    id: uuid.UUID
    kind: str
    #: Meta's own id. For an ad account this is the bare number; ``act_`` is added at the call.
    external_id: str
    name: str
    company_id: uuid.UUID | None
    currency: str | None
    timezone: str | None
    active: bool
    status: str


class MetaAssetProvider(Protocol):
    async def assets_for_company(
        self, ctx: Any, company_id: uuid.UUID, *, kind: str | None = None
    ) -> list[MetaAssetRef]:
        """Every active asset linked to this client. **Never picks one.**"""
        ...

    async def call_credentials(
        self, ctx: Any, asset_id: uuid.UUID
    ) -> tuple[MetaAssetRef, MetaCredentials]:
        """One asset plus the credentials to call it with, or raise
        :class:`MetaNotConfigured`."""
        ...


class _Unregistered:
    async def assets_for_company(
        self, ctx: Any, company_id: uuid.UUID, *, kind: str | None = None
    ) -> list[MetaAssetRef]:
        return []

    async def call_credentials(
        self, ctx: Any, asset_id: uuid.UUID
    ) -> tuple[MetaAssetRef, MetaCredentials]:
        raise MetaNotConfigured("meta assets are not available on this instance")


_provider: MetaAssetProvider = _Unregistered()


def register_meta_assets(provider: MetaAssetProvider) -> None:
    """Called once, at import, by the integration that owns ``meta_assets``."""
    global _provider
    _provider = provider


async def meta_assets_for_company(
    ctx: Any, company_id: uuid.UUID, *, kind: str | None = None
) -> list[MetaAssetRef]:
    return await _provider.assets_for_company(ctx, company_id, kind=kind)


async def meta_call_credentials(
    ctx: Any, asset_id: uuid.UUID
) -> tuple[MetaAssetRef, MetaCredentials]:
    return await _provider.call_credentials(ctx, asset_id)
