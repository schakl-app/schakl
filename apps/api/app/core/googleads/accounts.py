"""Who this client's Google Ads account is, and what it takes to call it.

The seam exists because two modules need the same answer and neither may import the other (§6).
``google_ads`` owns the row and registers the provider; ``marketing`` consumes it to draw a spend
tile. The shape is ``app.core.registrar``'s: a protocol in core, a registration from whichever
module holds the data, and a default that answers honestly when nobody has registered.

**The seam returns call parameters, never display values.** That distinction is the whole safety
argument. A ``marketing_links`` row carries ``external_id`` because the panel prints it and links
out to it, and ``SourceMetrics.external_id`` is typed ``str`` — a ``None`` there is a validation
error, and company panels compose with no per-panel ``try``, so one absent Ads account would 500
the *entire* company hub rather than blank one tile. So the borrower keeps its own display copy
and asks the seam only for the things a call needs: the customer id to address, the manager to
address it through, and the developer token to present.

**And absence raises rather than returning ``None``.** A ``None`` customer id reaches the URL
builder and asks Google about a customer named "None", which comes back 404 — which this
module's error model reads as *"the Ads API version is sunset"*, the single most misleading
sentence available for what is really an unlinked account. :class:`AdsNotConfigured` is a
presentable state every consumer already knows how to draw.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from app.config import settings
from app.core.googleads.client import AdsCredentials
from app.core.googleads.errors import AdsNotConfigured


@dataclass(frozen=True)
class AdsAccountRef:
    """One linked Google Ads account, as a borrower is allowed to see it."""

    id: uuid.UUID
    customer_id: str
    #: The manager (MCC) this account must be reached through, if any. Load-bearing: without it
    #: every call against a client account is made by a login with no direct grant on it.
    login_customer_id: str | None
    company_id: uuid.UUID | None
    #: The Google connection whose grant syncs this account. ``None`` = the link went dormant
    #: when its connection was removed and needs reconnecting.
    connection_id: uuid.UUID | None
    descriptive_name: str
    currency_code: str | None
    time_zone: str | None
    active: bool


@dataclass(frozen=True)
class AdsCallParams:
    """Everything one Ads call needs, resolved together so it is one read rather than three."""

    account: AdsAccountRef
    credentials: AdsCredentials

    @property
    def customer_id(self) -> str:
        return self.account.customer_id


class AdsAccountProvider(Protocol):
    """What the module owning ``google_ads_accounts`` answers for everyone else."""

    async def call_params(
        self,
        ctx: Any,
        *,
        account_id: uuid.UUID | None = None,
        customer_id: str | None = None,
    ) -> AdsCallParams:
        """Resolve one account plus the credentials to call it with, or raise
        :class:`AdsNotConfigured`."""
        ...

    async def accounts_for_company(self, ctx: Any, company_id: uuid.UUID) -> list[AdsAccountRef]:
        """Every active account linked to this client. **Never picks one** — a holding company
        and its trading name legitimately share an account, and one client can run two."""
        ...

    async def attach(
        self,
        ctx: Any,
        *,
        customer_id: str,
        company_id: uuid.UUID | None = None,
        login_customer_id: str | None = None,
        connection_id: uuid.UUID | None = None,
        descriptive_name: str = "",
        currency_code: str | None = None,
    ) -> AdsAccountRef:
        """Idempotent upsert on ``(org_id, customer_id)``.

        Idempotent on purpose: it is called from another module's write path, and an insert that
        could raise a unique violation there would turn a working ``POST /marketing/links`` into
        a 500 the moment two clients share an Ads account.
        """
        ...

    async def developer_token(self, ctx: Any) -> str:
        """The org's Google Ads developer token, or raise :class:`AdsNotConfigured`."""
        ...


class _Unregistered:
    """The answer when no module registered a provider — ``google_ads`` disabled or unlicensed.

    Registered by default so the seam is *always* callable. A registry that could be empty makes
    every call site write the same ``if provider is None`` and one of them eventually forgets;
    this way "nobody owns Ads accounts here" arrives as the same exception as "the token is not
    set", which is the same sentence on screen and the same handling in code.
    """

    async def call_params(
        self,
        ctx: Any,
        *,
        account_id: uuid.UUID | None = None,
        customer_id: str | None = None,
    ) -> AdsCallParams:
        raise AdsNotConfigured("google ads accounts are not available on this instance")

    async def accounts_for_company(self, ctx: Any, company_id: uuid.UUID) -> list[AdsAccountRef]:
        return []

    async def attach(self, ctx: Any, **kwargs: Any) -> AdsAccountRef:
        raise AdsNotConfigured("google ads accounts are not available on this instance")

    async def developer_token(self, ctx: Any) -> str:
        # The env var still answers, so an install that predates the module — or one that never
        # enables it — keeps whatever it had configured. It is the deprecated two-way door, not
        # the plan (docs/GOOGLE_ADS.md).
        token = (settings.google_ads_developer_token or "").strip()
        if not token:
            raise AdsNotConfigured("no google ads developer token configured")
        return token


_provider: AdsAccountProvider = _Unregistered()


def register_ads_accounts(provider: AdsAccountProvider) -> None:
    """Called once, at import, by the module that owns ``google_ads_accounts``."""
    global _provider
    _provider = provider


def ads_accounts_registered() -> bool:
    """Whether a real provider is installed — for a status screen, never for control flow.

    Control flow uses the exception: a provider that *is* registered but holds no token fails
    exactly the same way, and a caller that branched on this would handle only one of the two.
    """
    return not isinstance(_provider, _Unregistered)


async def ads_call_params(
    ctx: Any,
    *,
    account_id: uuid.UUID | None = None,
    customer_id: str | None = None,
) -> AdsCallParams:
    return await _provider.call_params(ctx, account_id=account_id, customer_id=customer_id)


async def ads_accounts_for_company(ctx: Any, company_id: uuid.UUID) -> list[AdsAccountRef]:
    return await _provider.accounts_for_company(ctx, company_id)


async def attach_ads_account(
    ctx: Any,
    *,
    customer_id: str,
    company_id: uuid.UUID | None = None,
    login_customer_id: str | None = None,
    connection_id: uuid.UUID | None = None,
    descriptive_name: str = "",
    currency_code: str | None = None,
) -> AdsAccountRef:
    return await _provider.attach(
        ctx,
        customer_id=customer_id,
        company_id=company_id,
        login_customer_id=login_customer_id,
        connection_id=connection_id,
        descriptive_name=descriptive_name,
        currency_code=currency_code,
    )


async def ads_developer_token(ctx: Any) -> str:
    return await _provider.developer_token(ctx)
