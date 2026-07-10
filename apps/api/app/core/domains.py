"""Custom-domain claim & verification for the current org (issue #26).

An org manager claims a domain, proves control via a DNS TXT record, and only then does the
domain start resolving to their org — an unverified claim never routes traffic, otherwise
anyone could park a competitor's hostname on their own org and phish it. Global uniqueness
(the one legitimately cross-tenant check) goes through ``app.core.instance.repo``, and every
step writes the instance audit trail.

The TXT challenge: ``_schakl-challenge.<domain>`` must contain the issued token.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import settings
from app.core import dnscheck
from app.core.instance import audit, repo
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

router = APIRouter(prefix="/meta/tenant/domain", tags=["meta"])

_HOSTNAME_RE = re.compile(
    r"^(?=.{4,255}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_CHALLENGE_PREFIX = "_schakl-challenge"


class DomainStatus(BaseModel):
    custom_domain: str | None
    custom_domain_verified_at: datetime | None
    pending_domain: str | None
    verification_token: str | None
    txt_record_name: str | None
    txt_record_value: str | None


class DomainClaim(BaseModel):
    domain: str = Field(min_length=4, max_length=255)


def _status(ctx: RequestContext) -> DomainStatus:
    org = ctx.org
    return DomainStatus(
        custom_domain=org.custom_domain,
        custom_domain_verified_at=org.custom_domain_verified_at,
        pending_domain=org.pending_domain,
        verification_token=org.domain_verification_token,
        txt_record_name=(
            f"{_CHALLENGE_PREFIX}.{org.pending_domain}" if org.pending_domain else None
        ),
        txt_record_value=org.domain_verification_token,
    )


@router.get(
    "",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.read")],
)
async def domain_status(ctx: RequestContext = Depends(require_context)) -> DomainStatus:
    return _status(ctx)


@router.post(
    "",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.write")],
)
async def claim_domain(
    payload: DomainClaim, ctx: RequestContext = Depends(require_context)
) -> DomainStatus:
    domain = payload.domain.strip().lower().rstrip(".")
    if not _HOSTNAME_RE.fullmatch(domain) or domain.endswith("." + settings.base_domain.lower()):
        # Hosts under the base domain are routed by slug; claiming one here could only
        # shadow another org.
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"domain": "errors.invalid_domain"},
        )
    if await repo.domain_taken(ctx.session, domain, exclude_org_id=ctx.org.id):
        raise AppError("domain_taken", "errors.domain_taken", status_code=409)

    ctx.org.pending_domain = domain
    ctx.org.domain_verification_token = secrets.token_hex(16)
    await ctx.session.flush()
    await audit.record(
        ctx.session, actor=ctx.user, action="domain.claim", org=ctx.org,
        detail={"domain": domain},
    )
    return _status(ctx)


@router.post(
    "/verify",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.write")],
)
async def verify_domain(ctx: RequestContext = Depends(require_context)) -> DomainStatus:
    org = ctx.org
    if not org.pending_domain or not org.domain_verification_token:
        raise AppError("not_found", "errors.not_found", status_code=404)
    records = await dnscheck.txt_records(f"{_CHALLENGE_PREFIX}.{org.pending_domain}")
    if org.domain_verification_token not in records:
        raise AppError(
            "domain_verification_failed", "errors.domain_verification_failed", status_code=400
        )
    # Re-check uniqueness at promotion time: another org may have verified it meanwhile.
    if await repo.domain_taken(ctx.session, org.pending_domain, exclude_org_id=org.id):
        raise AppError("domain_taken", "errors.domain_taken", status_code=409)

    org.custom_domain = org.pending_domain
    org.custom_domain_verified_at = datetime.now(UTC)
    org.pending_domain = None
    org.domain_verification_token = None
    await ctx.session.flush()
    await audit.record(
        ctx.session, actor=ctx.user, action="domain.verify", org=org,
        detail={"domain": org.custom_domain},
    )
    return _status(ctx)


@router.delete(
    "",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.write")],
)
async def clear_domain(ctx: RequestContext = Depends(require_context)) -> DomainStatus:
    """Remove the custom domain (and any pending claim). The org keeps resolving via
    ``<slug>.<base_domain>`` — the UI warns that this changes the org's address."""
    org = ctx.org
    cleared = org.custom_domain or org.pending_domain
    org.custom_domain = None
    org.custom_domain_verified_at = None
    org.pending_domain = None
    org.domain_verification_token = None
    await ctx.session.flush()
    await audit.record(
        ctx.session, actor=ctx.user, action="domain.clear", org=org,
        detail={"domain": cleared},
    )
    return _status(ctx)
