"""Custom-domain claim & verification for the current org (issue #26).

An org manager claims a domain, proves control via a DNS TXT record, and only then does the
domain start resolving to their org — an unverified claim never routes traffic, otherwise
anyone could park a competitor's hostname on their own org and phish it. Global uniqueness
(the one legitimately cross-tenant check) goes through ``app.core.instance.repo``, and every
step writes the instance audit trail.

The TXT challenge: ``_schakl-challenge.<domain>`` must contain the issued token.
"""

from __future__ import annotations

import logging
import re
import secrets
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.config import settings
from app.core import dnscheck, hosts
from app.core.instance import audit, repo
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

logger = logging.getLogger(__name__)

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
    # Cloud (#202): where the tenant points their CNAME so traffic reaches this instance
    # (TLS is issued automatically once verified). None on self-host — routing there is
    # the operator's own ingress concern.
    cname_target: str | None = None
    # Lifecycle state (#291), all None wherever Cloudflare doesn't manage the certificate.
    # Raw Cloudflare vocabulary ("active", "pending_validation", "moved", …) — external
    # system state, rendered as data by the UI, never translated.
    hostname_status: str | None = None
    ssl_status: str | None = None
    dns_ok: bool | None = None
    cert_expires_at: datetime | None = None
    checked_at: datetime | None = None
    check_error: str | None = None
    # The canonical-host decision (#291): verified is ownership, live is "actually serving".
    # While live, the custom domain is canonical (browser traffic on the slug host redirects
    # to it and generated links use it); the slug host below always keeps working as the
    # operator-controlled recovery path.
    live: bool = False
    canonical_host: str | None = None
    recovery_host: str | None = None


class DomainClaim(BaseModel):
    domain: str = Field(min_length=4, max_length=255)


def _cname_target() -> str | None:
    if not settings.is_cloud:
        return None
    from app.core.cloud.ingress import cname_target

    return cname_target()


async def _sync_cloud_ingress(ctx: RequestContext) -> None:
    """Keep the Traefik custom-domain fragment in step with a verify/clear (#202).
    No-op on self-host; never fails the request (sync_ingress logs and swallows)."""
    if not settings.is_cloud:
        return
    from app.core.cloud.ingress import sync_ingress

    await sync_ingress(ctx.session)


async def _register_cloudflare_hostname(domain: str) -> dict | None:
    """Register ``domain`` as a Cloudflare custom hostname; returns the full record, or None
    when the integration is off (every self-host install, and any cloud box without a token).

    Called **before** the org row is mutated, so a Cloudflare outage leaves the domain
    unverified rather than verified-but-unreachable. That is the honest failure: the customer
    retries, instead of being told their domain is live while the edge has no certificate for
    it. The clear path takes the opposite trade-off — see :func:`_release_cloudflare_hostname`.
    """
    from app.core.cloud.cloudflare import (
        CloudflareError,
        CloudflareNotEntitledError,
        cloudflare_configured,
        ensure_custom_hostname_record,
    )

    if not cloudflare_configured():
        return None
    try:
        return await ensure_custom_hostname_record(domain)
    except CloudflareNotEntitledError as exc:
        # A token scope or a plan entitlement (#293). "Try again in a moment" would be a lie —
        # nothing changes until the operator acts, so say so and log what they have to fix.
        logger.error(
            "cloudflare refused the custom hostname for %s — operator action required: %s",
            domain,
            exc,
        )
        raise AppError(
            "cloudflare_not_entitled", "errors.cloudflare_not_entitled", status_code=502
        ) from exc
    except CloudflareError as exc:
        logger.warning("cloudflare custom hostname failed for %s: %s", domain, exc)
        raise AppError(
            "cloudflare_failed", "errors.cloudflare_failed", status_code=502
        ) from exc


async def _release_cloudflare_hostname(hostname_id: str | None, domain: str | None) -> None:
    """Best-effort removal of the custom hostname behind a cleared domain.

    Unlike registration this never blocks the request: an org must always be able to drop its
    custom domain, and a leftover Cloudflare record is recoverable (the next verify adopts it,
    and it routes nothing meanwhile because ``orgs.custom_domain`` no longer resolves).
    """
    from app.core.cloud.cloudflare import (
        CloudflareError,
        cloudflare_configured,
        delete_custom_hostname,
    )

    if not hostname_id or not cloudflare_configured():
        return
    try:
        await delete_custom_hostname(hostname_id)
    except CloudflareError:
        logger.exception("could not delete cloudflare custom hostname for %s", domain)


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
        cname_target=_cname_target(),
        hostname_status=org.cf_hostname_status,
        ssl_status=org.cf_ssl_status,
        dns_ok=org.domain_dns_ok,
        cert_expires_at=org.domain_cert_expires_at,
        checked_at=org.domain_checked_at,
        check_error=org.domain_check_error,
        live=hosts.custom_domain_live(org),
        canonical_host=hosts.canonical_host(org),
        recovery_host=hosts.slug_host(org),
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

    # Cloudflare first, while the org row still says "unverified": if the edge cannot be
    # configured, nothing here claims the domain is live.
    record = await _register_cloudflare_hostname(org.pending_domain)

    org.custom_domain = org.pending_domain
    org.custom_domain_verified_at = datetime.now(UTC)
    org.cf_hostname_id = str(record["id"]) if record else None
    org.pending_domain = None
    org.domain_verification_token = None
    if record is not None:
        # Seed the lifecycle state (#291) from the create/adopt response: a fresh hostname
        # answers "pending", so the domain is verified (ownership proven, routed) but not
        # yet *live* — the canonical host stays the slug host until Cloudflare reports the
        # hostname and its certificate active (the check endpoint / daily sweep flip it).
        from app.core.cloud.domain_health import parse_hostname_record

        health = parse_hostname_record(record)
        org.cf_hostname_status = health.hostname_status
        org.cf_ssl_status = health.ssl_status
        org.domain_cert_expires_at = health.cert_expires_at
        org.domain_check_error = health.error
        org.domain_dns_ok = None
        org.domain_checked_at = datetime.now(UTC)
    else:
        # No Cloudflare lifecycle for this domain (Traefik/Let's Encrypt posture): make sure
        # no state from a previously managed domain lingers — verified is live here.
        org.cf_hostname_status = None
        org.cf_ssl_status = None
        org.domain_dns_ok = None
        org.domain_cert_expires_at = None
        org.domain_checked_at = None
        org.domain_check_error = None
    org.domain_alerted_for = None
    await ctx.session.flush()
    await audit.record(
        ctx.session, actor=ctx.user, action="domain.verify", org=org,
        detail={"domain": org.custom_domain},
    )
    await _sync_cloud_ingress(ctx)
    return _status(ctx)


@router.post(
    "/check",
    response_model=DomainStatus,
    dependencies=[require_permission("settings.domain.write")],
)
async def check_domain(ctx: RequestContext = Depends(require_context)) -> DomainStatus:
    """Reconcile the custom domain's lifecycle state on demand (#291).

    Fetches the Cloudflare custom-hostname status + certificate state and re-runs the DNS
    drift check, then stores the result — the same reconciliation the daily sweep performs,
    for the settings page's "check now" button. A no-op wherever Cloudflare does not manage
    the certificate: a Traefik/Let's Encrypt domain has no state to poll.
    """
    org = ctx.org
    if not org.custom_domain:
        raise AppError("not_found", "errors.not_found", status_code=404)
    from app.core.cloud.cloudflare import cloudflare_configured

    if org.cf_hostname_id and cloudflare_configured():
        from app.core.cloud.domain_health import refresh_domain_health

        # Two external lookups (Cloudflare + DNS); hand the pooled connection back while
        # they run (docs/PERFORMANCE.md). refresh mutates the loaded org only — memory,
        # not I/O — so nothing inside the block touches the session.
        async with ctx.release_db():
            await refresh_domain_health(org)
        await ctx.session.flush()
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
    hostname_id = org.cf_hostname_id
    org.custom_domain = None
    org.custom_domain_verified_at = None
    org.cf_hostname_id = None
    org.pending_domain = None
    org.domain_verification_token = None
    # Lifecycle state (#291) describes the cleared domain — drop it with the domain, so the
    # slug host (now canonical again) never renders a stale health warning.
    org.cf_hostname_status = None
    org.cf_ssl_status = None
    org.domain_dns_ok = None
    org.domain_cert_expires_at = None
    org.domain_checked_at = None
    org.domain_check_error = None
    org.domain_alerted_for = None
    await ctx.session.flush()
    await audit.record(
        ctx.session, actor=ctx.user, action="domain.clear", org=org,
        detail={"domain": cleared},
    )
    await _release_cloudflare_hostname(hostname_id, cleared)
    await _sync_cloud_ingress(ctx)
    return _status(ctx)
