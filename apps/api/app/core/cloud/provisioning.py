"""Provisioning API (epic #199, issue #201 slice). Business-licensed — see LICENSE here.

``/api/v1/instance/provisioning/*`` lets the operator's own machinery (a checkout, a billing
webhook, an internal CLI) auto-configure new cloud orgs without a browser session. It is
authenticated **only** by an instance API key (``X-API-Key`` or ``Authorization: Bearer``,
same ``schakl_…`` format as #20 but resolved against the instance-level table) — never by a
user cookie, and never reachable on a self-hosted box (mounted only when
``SCHAKL_DEPLOYMENT=cloud``). Mutations sit behind the ``cloud`` sku's write gate, so the
business license governs provisioning while the built-in bootstrap window still gives a
fresh install its trial (#137).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from pwdlib import PasswordHash
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.config import settings
from app.core import domainflow
from app.core.apikeys.keys import parse, verify_secret
from app.core.auth.models import User
from app.core.cloud.deps import require_cloud
from app.core.cloud.lifecycle import LifecycleUpdate, set_lifecycle
from app.core.cloud.models import PLANS, InstanceApiKey
from app.core.cloud.plans import set_plan
from app.core.instance import audit, repo
from app.core.instance import service as org_service
from app.core.models import Org, OrgStatus
from app.core.permissions.deps import no_permission_required
from app.db import async_session_maker
from app.errors import AppError

_password_hash = PasswordHash.recommended()


# --------------------------------------------------------------------------- #
# Instance-key authentication
# --------------------------------------------------------------------------- #
class ProvisioningContext:
    """The authenticated instance API key plus an unscoped session (like InstanceContext)."""

    def __init__(self, key: InstanceApiKey, session) -> None:  # noqa: ANN001
        self.key = key
        self.session = session
        self.actor = audit.SystemActor(email=f"key:{key.name}")


def _presented_key(request: Request) -> str | None:
    header = request.headers.get("x-api-key")
    if header:
        return header.strip()
    authorization = request.headers.get("authorization", "")
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() == "bearer" and value.strip().startswith("schakl_"):
        return value.strip()
    return None


async def require_provisioning_key(
    request: Request,
) -> AsyncGenerator[ProvisioningContext, None]:
    presented = _presented_key(request)
    parsed = parse(presented) if presented else None
    if parsed is None:
        raise AppError("unauthorized", "errors.unauthorized", status_code=401)
    prefix, secret = parsed
    async with async_session_maker() as session:
        key = await session.scalar(
            select(InstanceApiKey).where(InstanceApiKey.prefix == prefix)
        )
        now = datetime.now(UTC)
        if (
            key is None
            or key.revoked_at is not None
            or (key.expires_at is not None and key.expires_at < now)
            or not verify_secret(secret, key.hash)
        ):
            raise AppError("unauthorized", "errors.unauthorized", status_code=401)
        key.last_used_at = now
        ctx = ProvisioningContext(key, session)
        try:
            yield ctx
            await session.commit()
        except Exception:
            await session.rollback()
            raise


router = APIRouter(
    prefix="/instance/provisioning",
    tags=["provisioning"],
    # Authenticated by an instance API key, not a membership — the org axis does not exist
    # here (deny-by-default is satisfied by require_provisioning_key on every route).
    dependencies=[
        Depends(require_cloud),
        no_permission_required("cloud provisioning: gated on an instance API key"),
    ],
)


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
class ProvisionedOrg(BaseModel):
    id: str
    slug: str
    name: str
    status: str
    plan: str | None
    trial_ends_at: datetime | None
    url: str
    owner_email: str | None = None
    # Custom-domain state (#292). ``dns_records`` are the records the customer must create
    # (Type/Name/Value/TTL) — the operator's checkout can show or mail them verbatim.
    custom_domain: str | None = None
    custom_domain_active: bool = False
    pending_domain: str | None = None
    dns_records: list[domainflow.DnsRecordCard] = []


class ProvisionOrgRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=63)
    owner_email: EmailStr
    # Set for full auto-configuration (the caller is the operator's own machinery); omitted,
    # the owner arrives via the password-reset flow like any invited member.
    owner_password: str | None = Field(default=None, min_length=8, max_length=128)
    owner_full_name: str | None = Field(default=None, max_length=255)
    brand_name: str | None = Field(default=None, max_length=255)
    locale: str | None = None
    enabled_modules: list[str] | None = None
    # The operator's choice per org: a clocked free trial, billing-managed standard, or
    # no expiration at all.
    plan: str = Field(default="trial")
    trial_days: int | None = Field(default=None, ge=1, le=365)
    # Configure a custom domain in the same call (#292). ``activate`` = operator-asserted
    # ownership: the TXT challenge is skipped (audited as such), the edge hostname is
    # provisioned fail-closed, and the response's ``dns_records`` say what the customer's
    # DNS must carry. ``claim`` only reserves the name + issues the ownership challenge; the
    # org's admin finishes the wizard themselves.
    custom_domain: str | None = Field(default=None, min_length=4, max_length=255)
    custom_domain_mode: str = Field(default="activate", pattern="^(activate|claim)$")


class PlanUpdate(BaseModel):
    plan: str
    trial_days: int | None = Field(default=None, ge=1, le=365)
    trial_ends_at: datetime | None = None




def _org_url(org: Org) -> str:
    # The canonical host (#291): the custom domain only while it is actually live, so the
    # billing system never mails out a link whose edge cannot serve it.
    from app.core.hosts import canonical_host

    scheme = "https" if settings.auth_cookie_secure else "http"
    return f"{scheme}://{canonical_host(org)}"


def _provisioned(org: Org, owner_email: str | None = None) -> ProvisionedOrg:
    return ProvisionedOrg(
        id=str(org.id),
        slug=org.slug,
        name=org.name,
        status=org.status,
        plan=org.plan,
        trial_ends_at=org.trial_ends_at,
        url=_org_url(org),
        owner_email=owner_email,
        custom_domain=org.custom_domain,
        custom_domain_active=org.custom_domain_verified_at is not None,
        pending_domain=org.pending_domain,
        dns_records=domainflow.record_cards(org),
    )


async def _org_by_slug(ctx: ProvisioningContext, slug: str) -> Org:
    org = await ctx.session.scalar(select(Org).where(Org.slug == slug.strip().lower()))
    if org is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return org


# --------------------------------------------------------------------------- #
# Endpoints
# --------------------------------------------------------------------------- #
@router.post("/orgs", response_model=ProvisionedOrg, status_code=201)
async def provision_org(
    payload: ProvisionOrgRequest,
    ctx: ProvisioningContext = Depends(require_provisioning_key),
) -> ProvisionedOrg:
    if payload.plan not in PLANS:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"plan": "errors.validation"},
        )
    org = await org_service.create_org(
        ctx.session,
        ctx.actor,
        name=payload.name,
        slug=payload.slug,
        brand_name=payload.brand_name,
        locale=payload.locale,
        enabled_modules=payload.enabled_modules,
        owner_email=str(payload.owner_email),
    )
    await set_plan(
        ctx.session, ctx.actor, org, plan=payload.plan, trial_days=payload.trial_days
    )
    if payload.custom_domain:
        # Same transaction: a domain that cannot be configured rolls the whole org back, so
        # the billing system retries one idempotent call instead of repairing half an org.
        await domainflow.attach(
            ctx.session,
            ctx.actor,
            org,
            payload.custom_domain,
            activate=payload.custom_domain_mode == "activate",
        )
    # Full auto-configure: the caller may hand the owner a working password (their own
    # checkout already verified the email address). Without one, the owner sets a password
    # through the forgot-password flow, exactly like an invited member.
    owner = await ctx.session.scalar(
        select(User).where(func.lower(User.email) == str(payload.owner_email).lower())
    )
    if owner is not None:
        if payload.owner_password:
            owner.hashed_password = _password_hash.hash(payload.owner_password)
            owner.is_verified = True
        if payload.owner_full_name and not owner.full_name:
            owner.full_name = payload.owner_full_name
        await ctx.session.flush()
    return _provisioned(org, owner_email=str(payload.owner_email))


@router.get("/orgs", response_model=list[ProvisionedOrg])
async def list_provisioned_orgs(
    ctx: ProvisioningContext = Depends(require_provisioning_key),
) -> list[ProvisionedOrg]:
    return [_provisioned(org) for org in await repo.list_orgs(ctx.session)]


@router.get("/orgs/{slug}", response_model=ProvisionedOrg)
async def provisioned_org(
    slug: str, ctx: ProvisioningContext = Depends(require_provisioning_key)
) -> ProvisionedOrg:
    return _provisioned(await _org_by_slug(ctx, slug))


@router.patch("/orgs/{slug}/plan", response_model=ProvisionedOrg)
async def update_plan(
    slug: str,
    payload: PlanUpdate,
    ctx: ProvisioningContext = Depends(require_provisioning_key),
) -> ProvisionedOrg:
    org = await _org_by_slug(ctx, slug)
    await set_plan(
        ctx.session,
        ctx.actor,
        org,
        plan=payload.plan,
        trial_days=payload.trial_days,
        trial_ends_at=payload.trial_ends_at,
    )
    return _provisioned(org)


@router.patch("/orgs/{slug}/lifecycle", response_model=ProvisionedOrg)
async def update_lifecycle(
    slug: str,
    payload: LifecycleUpdate,
    ctx: ProvisioningContext = Depends(require_provisioning_key),
) -> ProvisionedOrg:
    """Set an org's end date from the billing system.

    Separate from ``/plan`` on purpose: a plan says *how* the org is billed, an end date says
    *until when* it exists. A lifetime deal is ``plan=unlimited`` with no end date; a fixed-term
    contract is ``plan=standard`` with one; the two are set by different events.
    """
    org = await _org_by_slug(ctx, slug)
    await set_lifecycle(
        ctx.session,
        ctx.actor,
        org,
        ends_at=payload.ends_at,
        grace=payload.grace_days,
        retention=payload.retention_days,
    )
    return _provisioned(org)


@router.post("/orgs/{slug}/suspend", response_model=ProvisionedOrg)
async def suspend_provisioned_org(
    slug: str, ctx: ProvisioningContext = Depends(require_provisioning_key)
) -> ProvisionedOrg:
    org = await _org_by_slug(ctx, slug)
    return _provisioned(
        await org_service.set_status(ctx.session, ctx.actor, org, OrgStatus.SUSPENDED)
    )


@router.post("/orgs/{slug}/activate", response_model=ProvisionedOrg)
async def activate_provisioned_org(
    slug: str, ctx: ProvisioningContext = Depends(require_provisioning_key)
) -> ProvisionedOrg:
    org = await _org_by_slug(ctx, slug)
    return _provisioned(
        await org_service.set_status(ctx.session, ctx.actor, org, OrgStatus.ACTIVE)
    )
