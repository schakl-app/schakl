"""REST surface for instance administration (issue #26): ``/api/v1/instance/*``.

Everything here sits behind :func:`app.core.instance.guard.require_instance_admin` — off by
default, instance owners only. Responses about one org bind the RLS GUC to exactly that org.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Request, Response, UploadFile
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.config import settings
from app.core import domainflow, hosts
from app.core.auth.backend import issue_session_token
from app.core.auth.models import User
from app.core.instance import audit, portability, repo, service
from app.core.instance import capabilities as caps
from app.core.instance.guard import (
    InstanceContext,
    ensure_org_data_access,
    load_principal,
    no_capability_required,
    require_capability,
    require_instance_admin,
)
from app.core.instance.impersonation import (
    IMPERSONATION_COOKIE,
    claim_handoff,
    clear_grant_cookie,
    create_handoff,
    issue_grant,
    set_grant_cookie,
)
from app.core.models import InstanceAuditLog, Membership, Org, OrgSettings, OrgStatus
from app.core.permissions.deps import no_permission_required
from app.core.permissions.models import MembershipRole
from app.core.permissions.models import Role as RoleRow
from app.core.permissions.service import collapse_to_legacy_role
from app.core.tenancy import request_hostname
from app.db import async_session_maker, set_current_org
from app.errors import AppError

router = APIRouter(
    prefix="/instance",
    tags=["instance"],
    # Gated on ``users.is_superuser`` (the instance owner, issue #26) — a different axis from a
    # membership's permissions, and deliberately not expressible as one.
    dependencies=[no_permission_required("instance administration: gated on users.is_superuser")],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class OrgSummary(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    created_at: datetime
    suspended_at: datetime | None
    deleted_at: datetime | None
    exported_at: datetime | None
    custom_domain: str | None
    custom_domain_verified: bool
    pending_domain: str | None
    # Canonical host (#291): where the org should be reached *right now* — the custom domain
    # only while it is live, else the slug host. The console's impersonation jump and org
    # links use this, so the operator lands on an origin that actually serves.
    canonical_host: str
    custom_domain_live: bool = False
    # Cloud plan (epic #199); both None on self-host / unmanaged orgs.
    plan: str | None = None
    trial_ends_at: datetime | None = None
    # Per-org end date (#199). ``ends_at`` None = unlimited; the rest are the computed
    # consequences, so the console never has to re-derive the schedule from the defaults.
    ends_at: datetime | None = None
    grace_days: int | None = None
    retention_days: int | None = None
    lifecycle_stage: str = "active"
    suspends_at: datetime | None = None
    terminates_at: datetime | None = None
    # May this org use the operator's own e-mail transport (epic #199)? Only bites while the
    # instance actually has one configured; the org still chooses whether to use it.
    email_included: bool = True


class OrgMember(BaseModel):
    user_id: str
    email: str
    full_name: str | None
    role: str
    is_active: bool


class OrgDetail(OrgSummary):
    brand_name: str | None
    default_locale: str | None
    enabled_modules: list[str]
    members: list[OrgMember]


class OrgCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=63)
    brand_name: str | None = Field(default=None, max_length=255)
    locale: str | None = None
    enabled_modules: list[str] | None = None
    # Optional first owner; invited like a member (password via forgot-password flow).
    owner_email: EmailStr | None = None
    # Included e-mail (epic #199): on unless the operator says otherwise, which is what an
    # org gets today. False leaves the org bring-your-own-transport.
    email_included: bool = True
    # Configure a custom domain in the same call (#292). ``activate`` is operator-asserted
    # ownership (the TXT challenge is skipped and audited as such; the domain routes as soon
    # as its DNS points at the edge); ``claim`` only pre-claims, so the org's own admin
    # resumes the wizard at the ownership step.
    custom_domain: str | None = Field(default=None, min_length=4, max_length=255)
    custom_domain_mode: str = Field(default="activate", pattern="^(activate|claim)$")


class OrgDomainUpdate(BaseModel):
    domain: str = Field(min_length=4, max_length=255)
    mode: str = Field(default="activate", pattern="^(activate|claim)$")


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=63)
    #: NULL = leave the entitlement alone, so a rename can never silently switch an org's
    #: included e-mail off (this is a partial update, not a wholesale PUT).
    email_included: bool | None = None


class OrgModulesUpdate(BaseModel):
    enabled_modules: list[str]


class PurgeRequest(BaseModel):
    confirm: str


class ImportRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=63)
    name: str | None = Field(default=None, max_length=255)
    data: dict[str, Any]


class ImportResult(BaseModel):
    org: OrgSummary
    tables: dict[str, int]


class ImpersonateRequest(BaseModel):
    user_id: uuid.UUID
    minutes: int = Field(default=30, ge=1)


class ImpersonateHandoff(BaseModel):
    """Where to send the browser, and the one-time ticket to present there (#288)."""

    host: str
    ticket: str
    expires_at: datetime


class ImpersonateResponse(BaseModel):
    cookie: str
    #: The grant itself — **only** when the caller is already on the org's own host, where the
    #: console can simply set the cookie. On any other host the grant does not exist yet: it is
    #: minted when the handoff below is redeemed, so nothing usable travels through the console
    #: or its redirect URL (#288).
    token: str | None = None
    expires_at: datetime
    handoff: ImpersonateHandoff | None = None


class ImpersonationClaimRequest(BaseModel):
    # Deliberately defaulted rather than required: a missing, garbled or stale ticket is a
    # *refusal* (403), not a 422 about a field name. The route is the one place on this surface
    # that answers without a session, so it must refuse like the rest of it.
    ticket: str = ""


class ImpersonationClaimResponse(BaseModel):
    """The two cookies the tenant host has to set, and how long both may live."""

    session_cookie: str
    session_token: str
    cookie: str
    token: str
    expires_at: datetime
    max_age: int
    #: Where to send the operator when the impersonation ends — the console's own hostname, which
    #: on cloud is the apex. Derived from the instance's configuration, never from the request, so
    #: it can't be steered into an open redirect; a **host** rather than a URL because only the
    #: caller knows the scheme and port the browser is actually using. ``None`` on a self-hosted
    #: box: the console there lives on a tenant host and only the operator knows which one.
    console_host: str | None


class AuditEntry(BaseModel):
    id: str
    actor_email: str
    action: str
    org_slug: str | None
    target_user_id: str | None
    detail: dict[str, Any]
    created_at: datetime


def _lifecycle_dates(org: Org) -> tuple[datetime | None, datetime | None]:
    """The computed suspend/terminate instants, so the console does not re-derive them.

    Imported lazily and only for an org that actually has an end date: the lifecycle module is
    business-licensed cloud code, and a self-hosted box (where ``ends_at`` is always NULL)
    must never load it — the same rule the worker's cron registration follows.
    """
    if org.ends_at is None:
        return None, None
    from app.core.cloud import lifecycle

    return lifecycle.suspend_at(org), lifecycle.terminate_at(org)


def _summary(org: Org) -> OrgSummary:
    suspends_at, terminates_at = _lifecycle_dates(org)
    return OrgSummary(
        id=str(org.id),
        slug=org.slug,
        name=org.name,
        status=org.status,
        created_at=org.created_at,
        suspended_at=org.suspended_at,
        deleted_at=org.deleted_at,
        exported_at=org.exported_at,
        custom_domain=org.custom_domain,
        custom_domain_verified=org.custom_domain_verified_at is not None,
        pending_domain=org.pending_domain,
        canonical_host=hosts.canonical_host(org),
        custom_domain_live=hosts.custom_domain_live(org),
        plan=org.plan,
        trial_ends_at=org.trial_ends_at,
        ends_at=org.ends_at,
        grace_days=org.grace_days,
        retention_days=org.retention_days,
        lifecycle_stage=org.lifecycle_stage,
        suspends_at=suspends_at,
        terminates_at=terminates_at,
        email_included=org.email_included,
    )


async def _org_or_404(ctx: InstanceContext, org_id: uuid.UUID) -> Org:
    org = await repo.get_org(ctx.session, org_id)
    if org is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return org


# --------------------------------------------------------------------------- #
# Org lifecycle
# --------------------------------------------------------------------------- #
@router.get(
    "/orgs",
    response_model=list[OrgSummary],
    dependencies=[require_capability(caps.ORGS_READ)],
)
async def list_orgs(
    ctx: InstanceContext = Depends(require_instance_admin),
) -> list[OrgSummary]:
    return [_summary(org) for org in await repo.list_orgs(ctx.session)]


@router.post(
    "/orgs",
    response_model=OrgSummary,
    status_code=201,
    dependencies=[require_capability(caps.ORGS_WRITE)],
)
async def create_org(
    payload: OrgCreate, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await service.create_org(
        ctx.session,
        ctx.user,
        name=payload.name,
        slug=payload.slug,
        brand_name=payload.brand_name,
        locale=payload.locale,
        enabled_modules=payload.enabled_modules,
        owner_email=payload.owner_email,
        email_included=payload.email_included,
    )
    if payload.custom_domain:
        # In the same transaction: a domain that cannot be configured rolls the org back,
        # so the caller retries the whole provisioning instead of patching up half an org.
        await domainflow.attach(
            ctx.session,
            ctx.user,
            org,
            payload.custom_domain,
            activate=payload.custom_domain_mode == "activate",
        )
    return _summary(org)


# --------------------------------------------------------------------------- #
# Custom domain (#292): operator-side configuration of one org's domain
# --------------------------------------------------------------------------- #
@router.get(
    "/orgs/{org_id}/domain",
    response_model=domainflow.DomainStatus,
    dependencies=[require_capability(caps.ORGS_READ)],
)
async def org_domain(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> domainflow.DomainStatus:
    """Routing state is platform data (it decides which hostname reaches the org), so this
    stays PIN-free like the org list — it exposes no tenant content."""
    return domainflow.status_for(await _org_or_404(ctx, org_id))


@router.put(
    "/orgs/{org_id}/domain",
    response_model=domainflow.DomainStatus,
    dependencies=[require_capability(caps.ORGS_WRITE)],
)
async def set_org_domain(
    org_id: uuid.UUID,
    payload: OrgDomainUpdate,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> domainflow.DomainStatus:
    org = await _org_or_404(ctx, org_id)
    await domainflow.attach(
        ctx.session, ctx.user, org, payload.domain, activate=payload.mode == "activate"
    )
    return domainflow.status_for(org)


@router.delete(
    "/orgs/{org_id}/domain",
    response_model=domainflow.DomainStatus,
    dependencies=[require_capability(caps.ORGS_WRITE)],
)
async def clear_org_domain(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> domainflow.DomainStatus:
    org = await _org_or_404(ctx, org_id)
    await domainflow.clear(ctx.session, ctx.user, org)
    return domainflow.status_for(org)


@router.get(
    "/orgs/{org_id}",
    response_model=OrgDetail,
    dependencies=[require_capability(caps.ORGS_READ)],
)
async def org_detail(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgDetail:
    org = await _org_or_404(ctx, org_id)
    # Tenant data (members, settings) — on cloud this is where the service PIN bites (#199).
    await ensure_org_data_access(ctx, org)
    # Settings and memberships are RLS-forced: bind the GUC to this one org to read them.
    await set_current_org(ctx.session, org.id)
    org_settings = await ctx.session.scalar(
        select(OrgSettings).where(OrgSettings.org_id == org.id)
    )
    rows = (
        await ctx.session.execute(
            select(Membership, User)
            .join(User, User.id == Membership.user_id)
            .where(Membership.org_id == org.id)
            .order_by(User.email.asc())
        )
    ).all()
    # Display role = the highest-privilege *system* role each membership holds (issue #56:
    # the legacy column is gone). One grouped query for the whole list, never one per member.
    system_keys: dict[uuid.UUID, list[str]] = {}
    for membership_id, key in await ctx.session.execute(
        select(MembershipRole.membership_id, RoleRow.key)
        .join(RoleRow, RoleRow.id == MembershipRole.role_id)
        .where(
            MembershipRole.org_id == org.id,
            RoleRow.is_system.is_(True),
        )
    ):
        system_keys.setdefault(membership_id, []).append(key)
    return OrgDetail(
        **_summary(org).model_dump(),
        brand_name=org_settings.brand_name if org_settings else None,
        default_locale=org_settings.default_locale if org_settings else None,
        enabled_modules=list(org_settings.enabled_modules) if org_settings else [],
        members=[
            OrgMember(
                user_id=str(user.id),
                email=user.email,
                full_name=user.full_name,
                role=collapse_to_legacy_role(system_keys.get(membership.id, [])),
                is_active=user.is_active,
            )
            for membership, user in rows
        ],
    )


@router.patch(
    "/orgs/{org_id}",
    response_model=OrgSummary,
    dependencies=[require_capability(caps.ORGS_WRITE)],
)
async def update_org(
    org_id: uuid.UUID,
    payload: OrgUpdate,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    org = await service.update_org(
        ctx.session,
        ctx.user,
        org,
        name=payload.name,
        slug=payload.slug,
        email_included=payload.email_included,
    )
    return _summary(org)


@router.post(
    "/orgs/{org_id}/suspend",
    response_model=OrgSummary,
    dependencies=[require_capability(caps.LIFECYCLE_WRITE)],
)
async def suspend_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    return _summary(await service.set_status(ctx.session, ctx.user, org, OrgStatus.SUSPENDED))


@router.post(
    "/orgs/{org_id}/activate",
    response_model=OrgSummary,
    dependencies=[require_capability(caps.LIFECYCLE_WRITE)],
)
async def activate_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    return _summary(await service.set_status(ctx.session, ctx.user, org, OrgStatus.ACTIVE))


@router.delete(
    "/orgs/{org_id}",
    response_model=OrgSummary,
    dependencies=[require_capability(caps.LIFECYCLE_WRITE)],
)
async def soft_delete_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    return _summary(await service.set_status(ctx.session, ctx.user, org, OrgStatus.DELETED))


@router.post(
    "/orgs/{org_id}/purge",
    status_code=204,
    dependencies=[require_capability(caps.ORGS_PURGE)],
)
async def purge_org(
    org_id: uuid.UUID,
    payload: PurgeRequest,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> None:
    org = await _org_or_404(ctx, org_id)
    await service.purge_org(ctx.session, ctx.user, org, confirm=payload.confirm)


@router.patch(
    "/orgs/{org_id}/modules",
    response_model=OrgDetail,
    dependencies=[require_capability(caps.ORGS_WRITE)],
)
async def update_org_modules(
    org_id: uuid.UUID,
    payload: OrgModulesUpdate,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> OrgDetail:
    org = await _org_or_404(ctx, org_id)
    await ensure_org_data_access(ctx, org)
    await service.set_org_modules(ctx.session, ctx.user, org, payload.enabled_modules)
    return await org_detail(org_id, ctx)


# --------------------------------------------------------------------------- #
# Data portability
# --------------------------------------------------------------------------- #
@router.get("/orgs/{org_id}/export", dependencies=[require_capability(caps.DATA_EXPORT)])
async def export_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> dict[str, Any]:
    org = await _org_or_404(ctx, org_id)
    await ensure_org_data_access(ctx, org)
    payload = await portability.export_org(ctx.session, org)
    org.exported_at = datetime.now(UTC)
    await ctx.session.flush()
    await audit.record(ctx.session, actor=ctx.user, action="org.export", org=org)
    return payload


@router.get("/orgs/{org_id}/archive", dependencies=[require_capability(caps.DATA_EXPORT)])
async def export_org_archive(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> Response:
    """The complete export: rows **and** stored bytes, as a zip.

    ``/export`` returns rows only, which is a pointer-shaped answer once files live in object
    storage. This is what an agency leaving should take, and what the automated termination
    archives before it destroys anything.
    """
    org = await _org_or_404(ctx, org_id)
    await ensure_org_data_access(ctx, org)
    blob = await portability.build_archive(ctx.session, org)
    org.exported_at = datetime.now(UTC)
    await ctx.session.flush()
    await audit.record(
        ctx.session, actor=ctx.user, action="org.export", org=org, detail={"format": "archive"}
    )
    return Response(
        content=blob,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{org.slug}-archive.zip"',
        },
    )


@router.post(
    "/orgs/import-archive",
    response_model=ImportResult,
    status_code=201,
    dependencies=[require_capability(caps.DATA_EXPORT)],
)
async def import_org_archive(
    slug: str = Form(...),
    name: str | None = Form(default=None),
    file: UploadFile = File(...),
    ctx: InstanceContext = Depends(require_instance_admin),
) -> ImportResult:
    """Restore an org from a zip archive — rows and bytes both."""
    slug = service.validate_slug(slug)
    if await repo.slug_taken(ctx.session, slug):
        raise AppError("slug_taken", "errors.slug_taken", status_code=409)
    payload, blobs = portability.read_archive(await file.read())
    org, counts = await portability.import_org(
        ctx.session, payload, slug=slug, name=name, files=blobs
    )
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="org.import",
        org=org,
        detail={"tables": counts, "files": len(blobs)},
    )
    return ImportResult(org=_summary(org), tables=counts)


@router.post(
    "/orgs/import",
    response_model=ImportResult,
    status_code=201,
    dependencies=[require_capability(caps.DATA_EXPORT)],
)
async def import_org(
    payload: ImportRequest, ctx: InstanceContext = Depends(require_instance_admin)
) -> ImportResult:
    slug = service.validate_slug(payload.slug)
    if await repo.slug_taken(ctx.session, slug):
        raise AppError("slug_taken", "errors.slug_taken", status_code=409)
    org, counts = await portability.import_org(
        ctx.session, payload.data, slug=slug, name=payload.name
    )
    await audit.record(
        ctx.session, actor=ctx.user, action="org.import", org=org, detail={"tables": counts}
    )
    return ImportResult(org=_summary(org), tables=counts)


# --------------------------------------------------------------------------- #
# Impersonation (audited, time-boxed, banner-visible via /meta/me)
# --------------------------------------------------------------------------- #
@router.post(
    "/orgs/{org_id}/impersonate",
    response_model=ImpersonateResponse,
    dependencies=[require_capability(caps.IMPERSONATE)],
)
async def impersonate(
    org_id: uuid.UUID,
    payload: ImpersonateRequest,
    request: Request,
    response: Response,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> ImpersonateResponse:
    org = await _org_or_404(ctx, org_id)
    await ensure_org_data_access(ctx, org)
    if org.status != OrgStatus.ACTIVE.value:
        raise AppError("conflict", "errors.conflict", status_code=409)
    await set_current_org(ctx.session, org.id)
    membership = await ctx.session.scalar(
        select(Membership).where(
            Membership.org_id == org.id, Membership.user_id == payload.user_id
        )
    )
    target = await ctx.session.get(User, payload.user_id)
    if membership is None or target is None or not target.is_active:
        raise AppError("not_found", "errors.not_found", status_code=404)

    # The canonical host (#291), not just the slug one: a custom domain whose certificate is
    # broken sends the operator to the recovery address instead of a TLS error — and
    # impersonation is exactly when someone is looking into that.
    host = hosts.canonical_host(org)
    if request_hostname(request) == host:
        # Already on the org's own hostname (a self-hosted box administering its own org): the
        # admin's session cookie is right here, so the grant can simply be set beside it.
        token, expires_at = issue_grant(ctx.user, target.id, org.id, payload.minutes)
        await _audit_impersonation_start(ctx, org, target, expires_at)
        set_grant_cookie(response, token, expires_at)
        return ImpersonateResponse(
            cookie=IMPERSONATION_COOKIE, token=token, expires_at=expires_at
        )

    # Crossing hosts (#288). Hand out nothing but a single-use ticket for that host; the grant
    # and the admin's session there are minted on redemption, in ``claim_impersonation``.
    ticket, ticket_expires_at = await create_handoff(
        ctx.session,
        admin=ctx.user,
        target_user_id=target.id,
        org_id=org.id,
        host=host,
        minutes=payload.minutes,
    )
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="impersonate.handoff",
        org=org,
        target_user_id=target.id,
        detail={"target_email": target.email, "host": host},
    )
    return ImpersonateResponse(
        cookie=IMPERSONATION_COOKIE,
        token=None,
        # The grant's clock starts at redemption, so all the console can promise is the window
        # the *ticket* is good for.
        expires_at=ticket_expires_at,
        handoff=ImpersonateHandoff(host=host, ticket=ticket, expires_at=ticket_expires_at),
    )


async def _audit_impersonation_start(
    ctx: InstanceContext, org: Org, target: User, expires_at: datetime
) -> None:
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="impersonate.start",
        org=org,
        target_user_id=target.id,
        detail={"target_email": target.email, "expires_at": expires_at.isoformat()},
    )


@router.post(
    "/impersonation/claim",
    response_model=ImpersonationClaimResponse,
    dependencies=[
        no_capability_required(
            "redeems a single-use handoff ticket the caller already holds, on the one host that "
            "ticket names (#288). It cannot be reached with a session at all — the whole point "
            "is that the administrator has none on the tenant's hostname yet — so the ticket "
            "itself is the credential: bound to host, org, impersonator and target, verified "
            "against the row, and refused (403) on any mismatch or second attempt. Every "
            "authorization decision was made when it was issued, behind instance.impersonate."
        )
    ],
)
async def claim_impersonation(
    request: Request, payload: ImpersonationClaimRequest
) -> ImpersonationClaimResponse:
    """Exchange a handoff ticket for the two cookies this host needs (#288).

    Deliberately session-less, and therefore paranoid: everything the issuing route checked is
    checked again here against live state, because the ticket may have been sitting in a redirect
    for two minutes. The refusal is one undifferentiated 403 — the browser is told the handoff
    failed, never which of the eight ways it did.
    """
    if not settings.instance_admin_enabled:
        # Same posture as the rest of the surface: a box with the flag off does not admit that
        # any of this exists.
        raise AppError("not_found", "errors.not_found", status_code=404)

    def refuse() -> AppError:
        return AppError(
            "impersonation_handoff_invalid",
            "errors.impersonation_handoff_invalid",
            status_code=403,
        )

    async with async_session_maker() as session:
        handoff = await claim_handoff(session, payload.ticket, request_hostname(request))
        if handoff is None:
            raise refuse()

        admin = await session.get(User, handoff.impersonator_user_id)
        if admin is None or not admin.is_active:
            raise refuse()
        # The administrator must *still* be one, and still hold the capability: unlike a grant in
        # flight (which lapses within its window — docs/CLOUD.md), a crossing that has not
        # happened yet is cheap to re-authorize, so it is.
        is_owner, capabilities = await load_principal(session, admin)
        principal = InstanceContext(
            user=admin, session=session, is_owner=is_owner, capabilities=capabilities
        )
        if not principal.can(caps.IMPERSONATE):
            raise refuse()

        org = await session.get(Org, handoff.org_id)
        if org is None or org.status != OrgStatus.ACTIVE.value:
            raise refuse()
        # …and the tenant's consent must still stand on cloud (the PIN can be revoked between
        # issuing and arriving).
        try:
            await ensure_org_data_access(principal, org)
        except AppError as exc:
            raise refuse() from exc

        await set_current_org(session, org.id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.org_id == org.id, Membership.user_id == handoff.target_user_id
            )
        )
        target = await session.get(User, handoff.target_user_id)
        if membership is None or target is None or not target.is_active:
            raise refuse()

        token, expires_at = issue_grant(admin, target.id, org.id, handoff.minutes)
        max_age = max(1, int((expires_at - datetime.now(UTC)).total_seconds()))
        # The admin's session on *this* host lapses with the grant: an operator's footprint on a
        # customer's hostname should not outlive the reason it was created.
        session_token = await issue_session_token(admin, max_age, org.id)
        await audit.record(
            session,
            actor=admin,
            action="impersonate.start",
            org=org,
            target_user_id=target.id,
            detail={
                "target_email": target.email,
                "expires_at": expires_at.isoformat(),
                "host": handoff.host,
                "via": "handoff",
            },
        )
        await session.commit()

    return ImpersonationClaimResponse(
        session_cookie=settings.auth_cookie_name,
        session_token=session_token,
        cookie=IMPERSONATION_COOKIE,
        token=token,
        expires_at=expires_at,
        max_age=max_age,
        # The apex serves the console on cloud (docs/CLOUD.md); a self-hosted box has no such
        # fixed address, so it gets nothing rather than a guess.
        console_host=settings.base_domain.lower() if settings.is_cloud else None,
    )


@router.post(
    "/impersonation/stop",
    status_code=204,
    dependencies=[
        no_capability_required(
            "ends the caller's OWN impersonation session by clearing their cookie. Requiring "
            "instance.impersonate here would trap someone whose capability was revoked "
            "mid-session in the very state the revocation was meant to end."
        )
    ],
)
async def stop_impersonation(
    response: Response, ctx: InstanceContext = Depends(require_instance_admin)
) -> None:
    await audit.record(ctx.session, actor=ctx.user, action="impersonate.stop")
    clear_grant_cookie(response)


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
@router.get(
    "/audit",
    response_model=list[AuditEntry],
    dependencies=[require_capability(caps.AUDIT_READ)],
)
async def list_audit(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: InstanceContext = Depends(require_instance_admin),
) -> list[AuditEntry]:
    rows = (
        (
            await ctx.session.execute(
                select(InstanceAuditLog)
                .order_by(InstanceAuditLog.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    return [
        AuditEntry(
            id=str(entry.id),
            actor_email=entry.actor_email,
            action=entry.action,
            org_slug=entry.org_slug,
            target_user_id=str(entry.target_user_id) if entry.target_user_id else None,
            detail=entry.detail,
            created_at=entry.created_at,
        )
        for entry in rows
    ]
