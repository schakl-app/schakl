"""REST endpoints for oxxa under ``/api/v1/oxxa`` (issue #296, CLAUDE.md §6, §9).

Deny-by-default: every route declares one of the three ``oxxa.*`` permissions (§15).

The read/act split shows up in the paths, as it does in ``cloudflare``: ``GET
/domains/{id}/status`` answers from stored rows and never calls OXXA, so a domain page loads at
full speed and still renders when the registrar is down; ``POST /domains/{id}/refresh`` is the
explicit "go look" action. Mirrors the domains module's own ``POST /{id}/refresh``
(docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.oxxa.schemas import (
    DomainRegistrarStatus,
    NameserverPush,
    NameserverPushResult,
    OxxaAccountCreate,
    OxxaAccountOption,
    OxxaAccountRead,
    OxxaAccountSyncResult,
    OxxaAccountUpdate,
    OxxaAccountVerifyResult,
    RegistrarDomainRead,
)
from app.modules.oxxa.service import OxxaService
from app.schemas import Page

router = APIRouter(prefix="/oxxa", tags=["oxxa"])


# --- accounts (the credential — the highest-blast-radius surface here) ------------------- #
@router.get(
    "/accounts",
    response_model=list[OxxaAccountRead],
    dependencies=[require_permission("oxxa.settings.manage")],
)
async def list_accounts(ctx: RequestContext = Depends(require_context)) -> list[OxxaAccountRead]:
    """Configured OXXA logins. The API password is never part of the response."""
    return [OxxaAccountRead(**row) for row in await OxxaService(ctx).list_accounts()]


@router.get(
    "/accounts/options",
    response_model=list[OxxaAccountOption],
    dependencies=[require_permission("oxxa.registrar.sync")],
)
async def list_account_options(
    ctx: RequestContext = Depends(require_context),
) -> list[OxxaAccountOption]:
    """Names only, for the "which register" picker — choosing one is the sync/push caller's
    job, and should not require holding the credential screen's permission."""
    return [OxxaAccountOption(**row) for row in await OxxaService(ctx).account_options()]


@router.post(
    "/accounts",
    response_model=OxxaAccountRead,
    status_code=201,
    dependencies=[require_permission("oxxa.settings.manage")],
)
async def create_account(
    payload: OxxaAccountCreate, ctx: RequestContext = Depends(require_context)
) -> OxxaAccountRead:
    """Store a credential. Creating does not verify it — ``/verify`` is the explicit probe, so
    a typo is reported on the settings screen rather than as a failed save."""
    account = await OxxaService(ctx).create_account(payload)
    return OxxaAccountRead.model_validate(account)


@router.patch(
    "/accounts/{account_id}",
    response_model=OxxaAccountRead,
    dependencies=[require_permission("oxxa.settings.manage")],
)
async def update_account(
    account_id: uuid.UUID,
    payload: OxxaAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> OxxaAccountRead:
    """Rename, repoint or rotate. An omitted ``api_password`` keeps the stored one."""
    account = await OxxaService(ctx).update_account(account_id, payload)
    return OxxaAccountRead.model_validate(account)


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[require_permission("oxxa.settings.manage")],
)
async def delete_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Forget the credential and the register synced from it. Nothing at OXXA is deleted."""
    await OxxaService(ctx).delete_account(account_id)


@router.post(
    "/accounts/{account_id}/verify",
    response_model=OxxaAccountVerifyResult,
    dependencies=[require_permission("oxxa.settings.manage")],
)
async def verify_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> OxxaAccountVerifyResult:
    """Probe the credential and cache the TLDs it may operate on.

    Also brings back the reseller balance: a register that has run out of credit stops renewing
    domains, and nothing else in schakl would ever mention it.
    """
    return await OxxaService(ctx).verify_account(account_id)


@router.post(
    "/accounts/{account_id}/sync",
    response_model=OxxaAccountSyncResult,
    dependencies=[require_permission("oxxa.registrar.sync")],
)
async def sync_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> OxxaAccountSyncResult:
    """Pull the whole register and reconcile it. One request to OXXA, not one per domain."""
    return await OxxaService(ctx).sync_account(account_id)


# --- the register --------------------------------------------------------------------- #
@router.get(
    "/domains",
    response_model=Page[RegistrarDomainRead],
    dependencies=[require_permission("oxxa.registrar.sync")],
)
async def list_register(
    ctx: RequestContext = Depends(require_context),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    account_id: uuid.UUID | None = None,
    linked: bool | None = Query(
        None, description="true = matched to a schakl domain, false = only the unmatched"
    ),
    q: str | None = Query(None, max_length=253),
    count: bool = Query(True, description="false skips the count query (docs/PERFORMANCE.md)"),
) -> Page[RegistrarDomainRead]:
    """The stored register. ``linked=false`` is the one worth looking at: domains the agency is
    paying to renew that no schakl record — and therefore no invoice — knows about."""
    items, total = await OxxaService(ctx).list_register(
        limit=limit, offset=offset, account_id=account_id, linked=linked, q=q, count=count
    )
    return Page[RegistrarDomainRead](
        items=[RegistrarDomainRead(**row) for row in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/domains/{domain_id}/status",
    response_model=DomainRegistrarStatus,
    dependencies=[require_permission("oxxa.registrar.sync")],
)
async def domain_status(
    domain_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> DomainRegistrarStatus:
    """Stored rows only — never calls OXXA, so the domain page renders when OXXA is down."""
    return DomainRegistrarStatus(**await OxxaService(ctx).domain_status(domain_id))


@router.post(
    "/domains/{domain_id}/refresh",
    response_model=DomainRegistrarStatus,
    dependencies=[require_permission("oxxa.registrar.sync")],
)
async def refresh_domain(
    domain_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
    account_id: uuid.UUID | None = None,
) -> DomainRegistrarStatus:
    """Re-read one domain from the registrar, including DNSSEC and the registrant's name."""
    return DomainRegistrarStatus(**await OxxaService(ctx).refresh_domain(domain_id, account_id))


@router.post(
    "/domains/{domain_id}/nameservers",
    response_model=NameserverPushResult,
    dependencies=[require_permission("oxxa.registrar.manage")],
)
async def push_nameservers(
    domain_id: uuid.UUID,
    payload: NameserverPush,
    ctx: RequestContext = Depends(require_context),
) -> NameserverPushResult:
    """Repoint the domain's delegation at the registrar.

    Its own permission, not ``domains.domain.write``: this changes where the world resolves a
    client's domain, which is a different blast radius from editing our record of it.
    Idempotent — pushing the delegation a domain already has writes nothing at OXXA.
    """
    return await OxxaService(ctx).push_nameservers(domain_id, payload)
