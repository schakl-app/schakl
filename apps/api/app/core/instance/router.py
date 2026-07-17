"""REST surface for instance administration (issue #26): ``/api/v1/instance/*``.

Everything here sits behind :func:`app.core.instance.guard.require_instance_admin` — off by
default, instance owners only. Responses about one org bind the RLS GUC to exactly that org.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.core.auth.models import User
from app.core.instance import audit, portability, repo, service
from app.core.instance.guard import (
    InstanceContext,
    ensure_org_data_access,
    require_instance_admin,
)
from app.core.instance.impersonation import (
    IMPERSONATION_COOKIE,
    clear_grant_cookie,
    issue_grant,
    set_grant_cookie,
)
from app.core.models import InstanceAuditLog, Membership, Org, OrgSettings, OrgStatus
from app.core.permissions.deps import no_permission_required
from app.core.permissions.models import MembershipRole
from app.core.permissions.models import Role as RoleRow
from app.core.permissions.service import collapse_to_legacy_role
from app.db import set_current_org
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
    # Cloud plan (epic #199); both None on self-host / unmanaged orgs.
    plan: str | None = None
    trial_ends_at: datetime | None = None


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


class OrgUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    slug: str | None = Field(default=None, min_length=1, max_length=63)


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


class ImpersonateResponse(BaseModel):
    cookie: str
    token: str
    expires_at: datetime


class AuditEntry(BaseModel):
    id: str
    actor_email: str
    action: str
    org_slug: str | None
    target_user_id: str | None
    detail: dict[str, Any]
    created_at: datetime


def _summary(org: Org) -> OrgSummary:
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
        plan=org.plan,
        trial_ends_at=org.trial_ends_at,
    )


async def _org_or_404(ctx: InstanceContext, org_id: uuid.UUID) -> Org:
    org = await repo.get_org(ctx.session, org_id)
    if org is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return org


# --------------------------------------------------------------------------- #
# Org lifecycle
# --------------------------------------------------------------------------- #
@router.get("/orgs", response_model=list[OrgSummary])
async def list_orgs(
    ctx: InstanceContext = Depends(require_instance_admin),
) -> list[OrgSummary]:
    return [_summary(org) for org in await repo.list_orgs(ctx.session)]


@router.post("/orgs", response_model=OrgSummary, status_code=201)
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
    )
    return _summary(org)


@router.get("/orgs/{org_id}", response_model=OrgDetail)
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


@router.patch("/orgs/{org_id}", response_model=OrgSummary)
async def update_org(
    org_id: uuid.UUID,
    payload: OrgUpdate,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    org = await service.update_org(
        ctx.session, ctx.user, org, name=payload.name, slug=payload.slug
    )
    return _summary(org)


@router.post("/orgs/{org_id}/suspend", response_model=OrgSummary)
async def suspend_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    return _summary(await service.set_status(ctx.session, ctx.user, org, OrgStatus.SUSPENDED))


@router.post("/orgs/{org_id}/activate", response_model=OrgSummary)
async def activate_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    return _summary(await service.set_status(ctx.session, ctx.user, org, OrgStatus.ACTIVE))


@router.delete("/orgs/{org_id}", response_model=OrgSummary)
async def soft_delete_org(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgSummary:
    org = await _org_or_404(ctx, org_id)
    return _summary(await service.set_status(ctx.session, ctx.user, org, OrgStatus.DELETED))


@router.post("/orgs/{org_id}/purge", status_code=204)
async def purge_org(
    org_id: uuid.UUID,
    payload: PurgeRequest,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> None:
    org = await _org_or_404(ctx, org_id)
    await service.purge_org(ctx.session, ctx.user, org, confirm=payload.confirm)


@router.patch("/orgs/{org_id}/modules", response_model=OrgDetail)
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
@router.get("/orgs/{org_id}/export")
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


@router.post("/orgs/import", response_model=ImportResult, status_code=201)
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
@router.post("/orgs/{org_id}/impersonate", response_model=ImpersonateResponse)
async def impersonate(
    org_id: uuid.UUID,
    payload: ImpersonateRequest,
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

    token, expires_at = issue_grant(ctx.user, target.id, org.id, payload.minutes)
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="impersonate.start",
        org=org,
        target_user_id=target.id,
        detail={"target_email": target.email, "expires_at": expires_at.isoformat()},
    )
    set_grant_cookie(response, token, expires_at)
    return ImpersonateResponse(cookie=IMPERSONATION_COOKIE, token=token, expires_at=expires_at)


@router.post("/impersonation/stop", status_code=204)
async def stop_impersonation(
    response: Response, ctx: InstanceContext = Depends(require_instance_admin)
) -> None:
    await audit.record(ctx.session, actor=ctx.user, action="impersonate.stop")
    clear_grant_cookie(response)


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
@router.get("/audit", response_model=list[AuditEntry])
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
