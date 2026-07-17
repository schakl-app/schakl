"""Cloud REST surfaces (epic #199). Business-licensed — see this directory's LICENSE.

Two routers, always mounted (a posture-independent OpenAPI spec) but answering 404 on a
self-hosted box via :func:`app.core.cloud.deps.require_cloud`:

- ``org_router`` — the *tenant's* side of service access (Instellingen → Service-toegang):
  an org admin issues/revokes the PIN that lets the instance owner in. Tenant-scoped,
  permission-gated like every settings surface.
- ``instance_router`` — the cloud additions to the instance surface: claiming a service
  PIN, per-org plan control for the console UI, instance API keys, and ``/instance/me``
  (the console runs on the apex host where no org — and so no ``/meta/me`` — exists).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.apikeys.keys import generate, redacted
from app.core.auth.models import User
from app.core.auth.users import current_active_user
from app.core.cloud import access
from app.core.cloud.deps import require_cloud
from app.core.cloud.models import InstanceApiKey
from app.core.cloud.plans import set_plan
from app.core.instance import audit
from app.core.instance.guard import InstanceContext, require_instance_admin
from app.core.instance.repo import get_org
from app.core.models import Org
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

# --------------------------------------------------------------------------- #
# Tenant side: Instellingen → Service-toegang
# --------------------------------------------------------------------------- #
# Note the dependency order on every route below: the permission first, the posture gate
# second — a member holding nothing gets the same 403 as on any settings surface
# (deny-by-default sweep), while the surface still 404s for authorized users on self-host.
org_router = APIRouter(prefix="/settings/service-access", tags=["service-access"])


class ServiceAccessStatus(BaseModel):
    #: Hours a fresh PIN is valid for (config; the UI renders it in the explainer).
    pin_hours: int
    #: A live (unexpired, unrevoked) grant exists.
    active: bool = False
    created_at: datetime | None = None
    expires_at: datetime | None = None
    created_by_email: str | None = None
    #: The instance owner has claimed the PIN — support access is currently unlocked.
    claimed: bool = False


class ServiceAccessIssued(ServiceAccessStatus):
    #: The PIN itself, shown exactly once (only its hash is stored).
    pin: str


def _status(grant, claimed: bool) -> ServiceAccessStatus:  # noqa: ANN001
    return ServiceAccessStatus(
        pin_hours=settings.cloud_service_pin_hours,
        active=grant is not None,
        created_at=grant.created_at if grant else None,
        expires_at=grant.expires_at if grant else None,
        created_by_email=grant.created_by_email if grant else None,
        claimed=claimed,
    )


@org_router.get(
    "",
    response_model=ServiceAccessStatus,
    dependencies=[
        require_permission("settings.service_access.manage"),
        Depends(require_cloud),
    ],
)
async def service_access_status(
    ctx: RequestContext = Depends(require_context),
) -> ServiceAccessStatus:
    grant = await access.active_grant(ctx.session, ctx.org.id)
    return _status(grant, claimed=grant is not None and grant.claimed_at is not None)


@org_router.post(
    "",
    response_model=ServiceAccessIssued,
    status_code=201,
    dependencies=[
        require_permission("settings.service_access.manage"),
        Depends(require_cloud),
    ],
)
async def issue_service_pin(
    ctx: RequestContext = Depends(require_context),
) -> ServiceAccessIssued:
    grant, pin = await access.issue_pin(ctx.session, ctx.org, ctx.user)
    return ServiceAccessIssued(pin=pin, **_status(grant, claimed=False).model_dump())


@org_router.delete(
    "",
    response_model=ServiceAccessStatus,
    dependencies=[
        require_permission("settings.service_access.manage"),
        Depends(require_cloud),
    ],
)
async def revoke_service_pin(
    ctx: RequestContext = Depends(require_context),
) -> ServiceAccessStatus:
    await access.revoke_pin(ctx.session, ctx.org, ctx.user)
    return _status(None, claimed=False)


# --------------------------------------------------------------------------- #
# Instance side: console additions
# --------------------------------------------------------------------------- #
instance_router = APIRouter(
    prefix="/instance",
    tags=["instance"],
    dependencies=[
        Depends(require_cloud),
        no_permission_required("instance administration: gated on users.is_superuser"),
    ],
)


class InstanceMe(BaseModel):
    """Who is logged in on the instance (apex) host — where no org, and so no ``/meta/me``,
    exists. The console guards itself on ``is_instance_admin``."""

    id: str
    email: str
    full_name: str | None
    is_instance_admin: bool
    is_instance_owner: bool


@instance_router.get("/me", response_model=InstanceMe)
async def instance_me(user: User = Depends(current_active_user)) -> InstanceMe:
    return InstanceMe(
        id=str(user.id),
        email=user.email,
        full_name=user.full_name,
        is_instance_admin=settings.instance_admin_enabled and user.is_superuser,
        is_instance_owner=user.is_superuser,
    )


class OrgServiceAccess(BaseModel):
    #: Whether this deployment requires a PIN at all (always true on cloud).
    required: bool = True
    #: When *this* instance owner's claimed access expires; None = locked.
    access_until: datetime | None = None
    #: A PIN has been issued by the org and is waiting to be claimed.
    pin_pending: bool = False


class ClaimPinRequest(BaseModel):
    pin: str = Field(min_length=4, max_length=32)


async def _org_or_404(ctx: InstanceContext, org_id: uuid.UUID) -> Org:
    org = await get_org(ctx.session, org_id)
    if org is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return org


@instance_router.get("/orgs/{org_id}/service-access", response_model=OrgServiceAccess)
async def org_service_access(
    org_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> OrgServiceAccess:
    org = await _org_or_404(ctx, org_id)
    until = await access.access_until(ctx.session, org.id, ctx.user.id)
    grant = await access.active_grant(ctx.session, org.id)
    return OrgServiceAccess(
        access_until=until,
        pin_pending=grant is not None and grant.claimed_at is None,
    )


@instance_router.post("/orgs/{org_id}/service-access", response_model=OrgServiceAccess)
async def claim_service_pin(
    org_id: uuid.UUID,
    payload: ClaimPinRequest,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> OrgServiceAccess:
    org = await _org_or_404(ctx, org_id)
    grant = await access.claim_pin(ctx, org, payload.pin)
    return OrgServiceAccess(access_until=grant.expires_at)


class OrgPlanUpdate(BaseModel):
    plan: str
    trial_days: int | None = Field(default=None, ge=1, le=365)
    trial_ends_at: datetime | None = None


class OrgPlanRead(BaseModel):
    plan: str | None
    trial_ends_at: datetime | None


@instance_router.patch("/orgs/{org_id}/plan", response_model=OrgPlanRead)
async def update_org_plan(
    org_id: uuid.UUID,
    payload: OrgPlanUpdate,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> OrgPlanRead:
    org = await _org_or_404(ctx, org_id)
    await set_plan(
        ctx.session,
        ctx.user,
        org,
        plan=payload.plan,
        trial_days=payload.trial_days,
        trial_ends_at=payload.trial_ends_at,
    )
    return OrgPlanRead(plan=org.plan, trial_ends_at=org.trial_ends_at)


# --------------------------------------------------------------------------- #
# Instance API keys (provisioning credentials)
# --------------------------------------------------------------------------- #
class InstanceApiKeyRead(BaseModel):
    id: str
    name: str
    key_redacted: str
    created_by_email: str
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None
    revoked_at: datetime | None


class InstanceApiKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    #: NULL = the key never expires — the operator's long-lived automation credential.
    expires_at: datetime | None = None


class InstanceApiKeyCreated(InstanceApiKeyRead):
    #: The full key, shown exactly once.
    secret: str


def _key_read(row: InstanceApiKey) -> InstanceApiKeyRead:
    return InstanceApiKeyRead(
        id=str(row.id),
        name=row.name,
        key_redacted=redacted(row.prefix),
        created_by_email=row.created_by_email,
        created_at=row.created_at,
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked_at=row.revoked_at,
    )


@instance_router.get("/api-keys", response_model=list[InstanceApiKeyRead])
async def list_instance_api_keys(
    ctx: InstanceContext = Depends(require_instance_admin),
) -> list[InstanceApiKeyRead]:
    rows = (
        (
            await ctx.session.execute(
                select(InstanceApiKey).order_by(InstanceApiKey.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    return [_key_read(row) for row in rows]


@instance_router.post("/api-keys", response_model=InstanceApiKeyCreated, status_code=201)
async def create_instance_api_key(
    payload: InstanceApiKeyCreate,
    ctx: InstanceContext = Depends(require_instance_admin),
) -> InstanceApiKeyCreated:
    generated = generate()
    row = InstanceApiKey(
        name=payload.name,
        prefix=generated.prefix,
        hash=generated.secret_hash,
        created_by_email=ctx.user.email,
        expires_at=payload.expires_at,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    await audit.record(
        ctx.session,
        actor=ctx.user,
        action="instance_key.create",
        detail={"name": payload.name, "prefix": generated.prefix},
    )
    return InstanceApiKeyCreated(secret=generated.plaintext, **_key_read(row).model_dump())


@instance_router.post("/api-keys/{key_id}/revoke", response_model=InstanceApiKeyRead)
async def revoke_instance_api_key(
    key_id: uuid.UUID, ctx: InstanceContext = Depends(require_instance_admin)
) -> InstanceApiKeyRead:
    row = await ctx.session.get(InstanceApiKey, key_id)
    if row is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await ctx.session.flush()
        await audit.record(
            ctx.session,
            actor=ctx.user,
            action="instance_key.revoke",
            detail={"name": row.name, "prefix": row.prefix},
        )
    return _key_read(row)
