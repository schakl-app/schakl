"""REST endpoints for cloudflare under ``/api/v1/cloudflare`` (epic #278, CLAUDE.md §6, §9).

Deny-by-default: every route declares one of the three ``cloudflare.*`` permissions (§15).

The read/act split is deliberate and shows up in the paths: ``GET /domains/{id}/status`` answers
from stored rows and never calls Cloudflare, so a domain page loads at full speed and still
renders when Cloudflare is down; ``POST /domains/{id}/check`` is the explicit "go look" action.
Mirrors the domains module's own ``POST /{id}/refresh`` (docs/PERFORMANCE.md).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.cloudflare.schemas import (
    AccountCreate,
    AccountOption,
    AccountRead,
    AccountSyncResult,
    AccountUpdate,
    AccountVerifyResult,
    ConnectRequest,
    DnsExport,
    DnsRecordRead,
    DnsRecordWrite,
    DomainStatusRead,
    PagesLinkCreate,
    PagesLinkRead,
    PagesProjectRead,
    RedirectAdopt,
    RedirectRead,
    RedirectRuleEdit,
    RedirectWrite,
    ZoneLink,
    ZoneRead,
    ZoneRecords,
)
from app.integrations.cloudflare.service import CloudflareService
from app.schemas import Page

router = APIRouter(prefix="/cloudflare", tags=["cloudflare"])


# --- accounts (credentials — the highest-blast-radius surface here) --------------------- #
@router.get(
    "/accounts",
    response_model=list[AccountRead],
    dependencies=[require_permission("cloudflare.settings.manage")],
)
async def list_accounts(ctx: RequestContext = Depends(require_context)) -> list[AccountRead]:
    """Configured Cloudflare accounts. The API token is never part of the response."""
    return [AccountRead(**row) for row in await CloudflareService(ctx).list_accounts()]


@router.get(
    "/accounts/options",
    response_model=list[AccountOption],
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def list_account_options(
    ctx: RequestContext = Depends(require_context),
) -> list[AccountOption]:
    """Names only, for the "which account" picker — choosing one is ``zone.manage``'s job, and
    should not require holding the credential screen's permission."""
    return [AccountOption(**row) for row in await CloudflareService(ctx).account_options()]


@router.post(
    "/accounts",
    response_model=AccountRead,
    status_code=201,
    dependencies=[require_permission("cloudflare.settings.manage")],
)
async def create_account(
    payload: AccountCreate, ctx: RequestContext = Depends(require_context)
) -> AccountRead:
    account = await CloudflareService(ctx).create_account(payload)
    return AccountRead.model_validate(account)


@router.patch(
    "/accounts/{account_id}",
    response_model=AccountRead,
    dependencies=[require_permission("cloudflare.settings.manage")],
)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> AccountRead:
    """Rename, repoint or rotate. An omitted ``api_token`` keeps the stored one."""
    account = await CloudflareService(ctx).update_account(account_id, payload)
    return AccountRead.model_validate(account)


@router.delete(
    "/accounts/{account_id}",
    status_code=204,
    dependencies=[require_permission("cloudflare.settings.manage")],
)
async def delete_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Forget the credential and its synced inventory. Nothing at Cloudflare is deleted."""
    await CloudflareService(ctx).delete_account(account_id)


@router.post(
    "/accounts/{account_id}/verify",
    response_model=AccountVerifyResult,
    dependencies=[require_permission("cloudflare.settings.manage")],
)
async def verify_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> AccountVerifyResult:
    """Probe what this token can do and store the answer, so the UI can name a missing scope."""
    return await CloudflareService(ctx).verify_account(account_id)


@router.post(
    "/accounts/{account_id}/sync",
    response_model=AccountSyncResult,
    dependencies=[require_permission("cloudflare.settings.manage")],
)
async def sync_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> AccountSyncResult:
    """Pull the account's zones and Pages projects, matching zones to domains by apex."""
    return await CloudflareService(ctx).sync_account(account_id)


# --- zones ------------------------------------------------------------------------------ #
@router.get(
    "/zones",
    response_model=Page[ZoneRead],
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def list_zones(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    account_id: uuid.UUID | None = Query(None),
    domain_id: uuid.UUID | None = Query(None),
    linked: bool | None = Query(None, description="true: matched to a domain; false: orphans"),
    q: str | None = Query(None, max_length=253),
    count: bool = Query(True, description="Compute the total. False for pickers."),
    ctx: RequestContext = Depends(require_context),
) -> Page[ZoneRead]:
    items, total = await CloudflareService(ctx).list_zones(
        limit=limit,
        offset=offset,
        account_id=account_id,
        domain_id=domain_id,
        linked=linked,
        q=q,
        count=count,
    )
    return Page(
        items=[ZoneRead(**row) for row in items], total=total, limit=limit, offset=offset
    )


@router.post(
    "/zones/{zone_id}/link",
    response_model=ZoneRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def link_zone(
    zone_id: uuid.UUID, payload: ZoneLink, ctx: RequestContext = Depends(require_context)
) -> ZoneRead:
    """Match a synced zone to a domain by hand, where the apex did not match automatically."""
    service = CloudflareService(ctx)
    zone = await service.link_zone(zone_id, payload.domain_id)
    return ZoneRead(**await service.zone_row(zone))


@router.delete(
    "/zones/{zone_id}/link",
    response_model=ZoneRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def unlink_zone(
    zone_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> ZoneRead:
    """Forget the match. The zone keeps serving at Cloudflare."""
    service = CloudflareService(ctx)
    zone = await service.unlink_zone(zone_id)
    return ZoneRead(**await service.zone_row(zone))


# --- DNS -------------------------------------------------------------------------------- #
@router.get(
    "/zones/{zone_id}/dns",
    response_model=ZoneRecords,
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def list_dns(
    zone_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> ZoneRecords:
    """The zone's records, read live from Cloudflare — never a cached copy (see schemas)."""
    return ZoneRecords(**await CloudflareService(ctx).list_dns(zone_id))


@router.get(
    "/zones/{zone_id}/dns/export",
    response_model=DnsExport,
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def export_dns(
    zone_id: uuid.UUID,
    fmt: str = Query("bind", pattern="^(bind|csv)$", alias="format"),
    ctx: RequestContext = Depends(require_context),
) -> DnsExport:
    """The zone as a BIND file (Cloudflare's own export) or a CSV built here."""
    return DnsExport(**await CloudflareService(ctx).export_dns(zone_id, fmt))


@router.post(
    "/zones/{zone_id}/dns",
    response_model=DnsRecordRead,
    status_code=201,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def create_dns_record(
    zone_id: uuid.UUID,
    payload: DnsRecordWrite,
    ctx: RequestContext = Depends(require_context),
) -> DnsRecordRead:
    return DnsRecordRead(**await CloudflareService(ctx).create_dns_record(zone_id, payload))


@router.patch(
    "/zones/{zone_id}/dns/{record_id}",
    response_model=DnsRecordRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def update_dns_record(
    zone_id: uuid.UUID,
    record_id: str,
    payload: DnsRecordWrite,
    ctx: RequestContext = Depends(require_context),
) -> DnsRecordRead:
    return DnsRecordRead(
        **await CloudflareService(ctx).update_dns_record(zone_id, record_id, payload)
    )


@router.delete(
    "/zones/{zone_id}/dns/{record_id}",
    status_code=204,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def delete_dns_record(
    zone_id: uuid.UUID, record_id: str, ctx: RequestContext = Depends(require_context)
) -> None:
    await CloudflareService(ctx).delete_dns_record(zone_id, record_id)


# --- Pages ------------------------------------------------------------------------------- #
@router.get(
    "/pages/projects",
    response_model=list[PagesProjectRead],
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def list_pages_projects(
    account_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> list[PagesProjectRead]:
    """Synced Pages projects — the picker's source, so it never waits on Cloudflare."""
    return [
        PagesProjectRead(**row)
        for row in await CloudflareService(ctx).list_pages_projects(account_id=account_id)
    ]


@router.delete(
    "/pages/links/{link_id}",
    status_code=204,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def unlink_pages_project(
    link_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Detach the hostname from the project. Its DNS record is left alone on purpose."""
    await CloudflareService(ctx).unlink_pages_project(link_id)


# --- domain-centric actions --------------------------------------------------------------- #
@router.get(
    "/domains/{domain_id}/status",
    response_model=DomainStatusRead,
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def cloudflare_domain_status(
    domain_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> DomainStatusRead:
    """Stored state only — no Cloudflare call, so this is safe on a page load."""
    return DomainStatusRead(**await CloudflareService(ctx).domain_status(domain_id, live=False))


@router.post(
    "/domains/{domain_id}/check",
    response_model=DomainStatusRead,
    dependencies=[require_permission("cloudflare.dns.read")],
)
async def cloudflare_check_domain(
    domain_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> DomainStatusRead:
    """Ask Cloudflare what it actually has: drift, conflicting redirects, whether the apex is
    proxied at all. Persists the observation; every probe fails softly and names itself."""
    return DomainStatusRead(**await CloudflareService(ctx).domain_status(domain_id, live=True))


@router.post(
    "/domains/{domain_id}/connect",
    response_model=ZoneRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def connect_domain(
    domain_id: uuid.UUID,
    payload: ConnectRequest,
    ctx: RequestContext = Depends(require_context),
) -> ZoneRead:
    """Adopt this domain's existing Cloudflare zone, or create one. Adoption always wins."""
    service = CloudflareService(ctx)
    result = await service.connect_domain(domain_id, payload)
    return ZoneRead(**await service.zone_row(result["zone"]))


@router.put(
    "/domains/{domain_id}/redirect",
    response_model=RedirectRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def set_redirect(
    domain_id: uuid.UUID,
    payload: RedirectWrite,
    ctx: RequestContext = Depends(require_context),
) -> RedirectRead:
    """Set the domain-wide redirect and push it to Cloudflare as a Redirect Rule."""
    redirect = await CloudflareService(ctx).set_redirect(domain_id, payload)
    return RedirectRead.model_validate(redirect)


@router.post(
    "/domains/{domain_id}/redirect/adopt",
    response_model=RedirectRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def adopt_redirect(
    domain_id: uuid.UUID,
    payload: RedirectAdopt,
    ctx: RequestContext = Depends(require_context),
) -> RedirectRead:
    """Take ownership of a Redirect Rule the zone already has. Writes nothing at Cloudflare."""
    redirect = await CloudflareService(ctx).adopt_redirect(domain_id, payload)
    return RedirectRead.model_validate(redirect)


@router.delete(
    "/domains/{domain_id}/redirect",
    status_code=204,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def remove_redirect(
    domain_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Delete the rule we created at Cloudflare. Rules we did not create are never touched."""
    await CloudflareService(ctx).remove_redirect(domain_id)


# --- one rule on the zone, by id ------------------------------------------------------------ #
# The two routes that make an *inherited* redirect a first-class row rather than a read-only
# finding. They name a rule Cloudflare holds — ours or the tenant's — and both answer with the
# refreshed report: the write has just invalidated the observation the caller's list was drawn
# from, so handing back a rule would leave the screen describing the zone as it was.
@router.put(
    "/domains/{domain_id}/redirect/rules/{rule_id}",
    response_model=DomainStatusRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def edit_zone_redirect(
    domain_id: uuid.UUID,
    rule_id: str,
    payload: RedirectRuleEdit,
    ctx: RequestContext = Depends(require_context),
) -> DomainStatusRead:
    """Change where an existing Redirect Rule sends traffic. Never changes what it matches."""
    return DomainStatusRead(**await CloudflareService(ctx).edit_zone_redirect(
        domain_id, rule_id, payload
    ))


@router.delete(
    "/domains/{domain_id}/redirect/rules/{rule_id}",
    response_model=DomainStatusRead,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def delete_zone_redirect(
    domain_id: uuid.UUID, rule_id: str, ctx: RequestContext = Depends(require_context)
) -> DomainStatusRead:
    """Delete one Redirect Rule from this zone by id, resolved inside the zone's own ruleset."""
    return DomainStatusRead(**await CloudflareService(ctx).delete_zone_redirect(domain_id, rule_id))


@router.post(
    "/domains/{domain_id}/pages",
    response_model=PagesLinkRead,
    status_code=201,
    dependencies=[require_permission("cloudflare.zone.manage")],
)
async def link_pages_project(
    domain_id: uuid.UUID,
    payload: PagesLinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> PagesLinkRead:
    """Serve a hostname of this domain from a Pages project (registers it *and* points DNS)."""
    return PagesLinkRead(**await CloudflareService(ctx).link_pages_project(domain_id, payload))
