"""Custom-domain wizard endpoints for the current org (issues #26, #292).

Thin REST surface over :mod:`app.core.domainflow` — a staged, resumable flow: claim →
prove ownership (DNS TXT) → point traffic DNS / certificate issuance → active. ``GET``
reads the persisted state without touching the network (SSR loads stay fast, and the
wizard resumes across sessions from it); ``POST /check`` is the single probe-and-advance
action the wizard polls, returning per-layer diagnostics instead of one generic error.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.core import domainflow
from app.core.domainflow import DomainCheckReport, DomainStatus
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/meta/tenant/domain", tags=["meta"])


class DomainClaim(BaseModel):
    domain: str = Field(min_length=4, max_length=255)


@router.get(
    "",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.read")],
)
async def domain_status(ctx: RequestContext = Depends(require_context)) -> DomainStatus:
    return domainflow.status_for(ctx.org)


@router.post(
    "",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.write")],
)
async def claim_domain(
    payload: DomainClaim, ctx: RequestContext = Depends(require_context)
) -> DomainStatus:
    await domainflow.claim(ctx.session, ctx.user, ctx.org, payload.domain)
    return domainflow.status_for(ctx.org)


@router.post(
    "/check",
    response_model=DomainCheckReport,
    dependencies=[require_permission("settings.domain.write")],
)
async def check_domain(ctx: RequestContext = Depends(require_context)) -> DomainCheckReport:
    """Probe the current stage's DNS/edge conditions, advance whatever they satisfy, and
    report each layer separately (ownership TXT, traffic DNS, hostname, certificate).

    Deliberately a 200 even when nothing is satisfied yet: "your record has not propagated"
    is a diagnostic, not an HTTP failure — the old single-shot verify's 400 is exactly the
    collapsed error #292 replaces.
    """
    return await domainflow.run_checks(ctx.session, ctx.user, ctx.org)


@router.delete(
    "",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.write")],
)
async def clear_domain(ctx: RequestContext = Depends(require_context)) -> DomainStatus:
    """Remove the custom domain (and any pending claim). The org keeps resolving via
    ``<slug>.<base_domain>`` — the UI warns that this changes the org's address."""
    await domainflow.clear(ctx.session, ctx.user, ctx.org)
    return domainflow.status_for(ctx.org)
