"""Staged custom-domain onboarding (issue #292; supersedes the single-shot verify of #26).

The flow is a resumable state machine on the ``orgs`` domain columns:

``none`` → **claim** → ``ownership_pending`` (prove control via the ``_schakl-challenge``
TXT record) → ``routing_pending`` (point traffic DNS at the edge; certificate issuance) →
``active``. Ownership is proven *before* the customer is asked to cut traffic over — an
unverified claim never routes (Golden Rule of #26), and a Cloudflare hostname is only
provisioned for a domain whose owner asked for it.

Every probe returns a :class:`DomainCheck` with a machine code, what was expected, and what
was actually observed — never a bare "verification failed". DNS conditions that plausibly
mean "still propagating" (missing record, NXDOMAIN, timeout) read as ``pending``; conditions
that need a correction (wrong value, wrong target, SERVFAIL, a rejected certificate) read as
``failed``. Transitions are attempted inside the same check call, so one "Check" click can
carry a customer from ownership straight through to active when everything is already in
place — and a page refresh resumes wherever the columns say the org is.

This module owns the logic; ``app.core.domains`` (tenant) and the instance/provisioning
surfaces are thin routers over it. Instance imports stay module-level-safe: nothing here
imports the instance routers, only ``repo``/``audit``.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import NamedTuple

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import dnscheck, domainprobe, hosts
from app.core.instance import audit, repo
from app.core.models import Org
from app.errors import AppError

logger = logging.getLogger(__name__)

CHALLENGE_PREFIX = "_schakl-challenge"

_HOSTNAME_RE = re.compile(r"^(?=.{4,255}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

#: Stages of the wizard, derived from the org columns — never stored.
STAGE_NONE = "none"
STAGE_OWNERSHIP = "ownership_pending"
STAGE_ROUTING = "routing_pending"
STAGE_ACTIVE = "active"

#: Advisory DNS-provider detection from NS records (#292): tailors copy-paste instructions
#: only. Substring match against the lowercased nameserver hostnames.
_PROVIDERS: tuple[tuple[str, str, str], ...] = (
    ("cloudflare.com", "cloudflare", "Cloudflare"),
    ("transip", "transip", "TransIP"),
    ("awsdns", "route53", "Amazon Route 53"),
    ("googledomains.com", "google", "Google Cloud DNS"),
    ("azure-dns", "azure", "Microsoft Azure DNS"),
    ("domaincontrol.com", "godaddy", "GoDaddy"),
    ("registrar-servers.com", "namecheap", "Namecheap"),
    ("ovh.net", "ovh", "OVH"),
    ("digitalocean.com", "digitalocean", "DigitalOcean"),
    ("hetzner", "hetzner", "Hetzner"),
    ("versio", "versio", "Versio"),
    ("openprovider", "openprovider", "Openprovider"),
    ("stratoserver", "strato", "Strato"),
    ("strato", "strato", "Strato"),
    ("mijndomein", "mijndomein", "Mijndomein"),
)


# --------------------------------------------------------------------------- #
# Schemas (shared by the tenant router, the instance console and provisioning)
# --------------------------------------------------------------------------- #
class DnsRecordCard(BaseModel):
    """One record to create at the customer's DNS provider, renderable as a copy-paste card."""

    #: What the record is for: ``ownership`` (TXT challenge) or ``traffic`` (CNAME).
    purpose: str
    type: str
    #: Fully-qualified record name, and the relative host for panels that append the zone.
    name: str
    host: str
    value: str
    ttl: int = 3600
    #: Ownership TXT may be removed once the domain is active; traffic records must remain.
    temporary: bool = False


class DomainCheck(BaseModel):
    """One probe's outcome, with the diagnostic answer #292 demands: which layer, what was
    expected, what was observed, and (via ``code`` → i18n) what to do next."""

    key: str  # ownership | dns_target | hostname | certificate
    state: str  # ok | pending | failed
    code: str
    message_key: str
    expected: str | None = None
    observed: str | None = None


class DomainStatus(BaseModel):
    stage: str
    custom_domain: str | None
    custom_domain_verified_at: datetime | None
    pending_domain: str | None
    ownership_verified_at: datetime | None
    #: Whether the domain being set up (pending, else active) looks like a zone apex — the
    #: web explains that an apex needs ALIAS/ANAME/flattening instead of a plain CNAME.
    apex: bool | None = None
    records: list[DnsRecordCard] = []
    #: Cloud only: where traffic must point. None on self-host (routing is the operator's).
    cname_target: str | None = None
    # ----------------------------------------------------------------- #
    # Post-activation lifecycle (#291). The wizard ends at ``active``; these say whether the
    # domain *stays* served. All None wherever Cloudflare does not manage the certificate.
    # Raw Cloudflare vocabulary ("active", "pending_validation", "moved", …) — external
    # system state, rendered as data by the UI, never translated.
    # ----------------------------------------------------------------- #
    hostname_status: str | None = None
    ssl_status: str | None = None
    dns_ok: bool | None = None
    cert_expires_at: datetime | None = None
    checked_at: datetime | None = None
    check_error: str | None = None
    #: The canonical-host decision (#291): ``active`` is ownership proven and routed, ``live``
    #: is "actually serving". While live the custom domain is canonical (browser traffic on
    #: the slug host redirects to it and generated links use it); ``recovery_host`` always
    #: keeps resolving as the operator-controlled way back in.
    live: bool = False
    canonical_host: str | None = None
    recovery_host: str | None = None


class DomainCheckReport(BaseModel):
    status: DomainStatus
    checked_at: datetime
    #: Correlate a customer-visible outcome with the operator's logs without leaking them.
    correlation_id: str
    #: Advisory NS-derived provider (key + display name) and the zone apex found. Detection
    #: is never authorization to edit anyone's DNS — it only tailors instructions.
    provider: str | None = None
    provider_name: str | None = None
    zone: str | None = None
    #: A stage transition happened during this check (the web advances its step).
    advanced: bool = False
    checks: list[DomainCheck] = []


def _check(
    key: str,
    state: str,
    code: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> DomainCheck:
    return DomainCheck(
        key=key,
        state=state,
        code=code,
        message_key=f"settings.domain.diag.{code}",
        expected=expected,
        observed=observed,
    )


# --------------------------------------------------------------------------- #
# Derived state
# --------------------------------------------------------------------------- #
def normalize_domain(raw: str) -> str:
    """Lowercase, trim, strip the trailing dot; refuse invalid names and anything under the
    base domain (those route by slug — claiming one could only shadow another org)."""
    domain = raw.strip().lower().rstrip(".")
    if domain.startswith("http://") or domain.startswith("https://"):
        domain = domain.split("//", 1)[1]
    domain = domain.split("/", 1)[0].split(":", 1)[0]
    base = settings.base_domain.lower()
    if not _HOSTNAME_RE.fullmatch(domain) or domain == base or domain.endswith("." + base):
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"domain": "errors.invalid_domain"},
        )
    return domain


def stage(org: Org) -> str:
    if org.pending_domain:
        if org.pending_domain_ownership_verified_at is None:
            return STAGE_OWNERSHIP
        return STAGE_ROUTING
    if org.custom_domain and org.custom_domain_verified_at is not None:
        return STAGE_ACTIVE
    return STAGE_NONE


def _cname_target() -> str | None:
    if not settings.is_cloud:
        return None
    from app.core.cloud.ingress import cname_target

    return cname_target()


def _relative_host(name: str, domain: str, zone: str | None) -> str:
    """The host label a DNS panel that appends the zone expects. Without NS detection the
    registrable zone is guessed as the last two labels — advisory, like everything here."""
    apex = zone or ".".join(domain.rsplit(".", 2)[-2:])
    if name == apex:
        return "@"
    if name.endswith("." + apex):
        return name[: -(len(apex) + 1)]
    return name


def _is_apex(domain: str, zone: str | None = None) -> bool:
    if zone is not None:
        return domain == zone
    return domain.count(".") == 1


def record_cards(org: Org, *, zone: str | None = None) -> list[DnsRecordCard]:
    """The stage-appropriate records. Ownership first, alone (#292: never ask for the traffic
    cutover before control is proven); the TXT stays listed during routing, flagged temporary,
    so the customer knows which record may be cleaned up afterwards."""
    cards: list[DnsRecordCard] = []
    current = stage(org)
    target = _cname_target()
    if current in (STAGE_OWNERSHIP, STAGE_ROUTING) and org.domain_verification_token:
        name = f"{CHALLENGE_PREFIX}.{org.pending_domain}"
        cards.append(
            DnsRecordCard(
                purpose="ownership",
                type="TXT",
                name=name,
                host=_relative_host(name, org.pending_domain, zone),
                value=org.domain_verification_token,
                temporary=True,
            )
        )
    if target is not None:
        domain = org.pending_domain if current == STAGE_ROUTING else None
        if current == STAGE_ACTIVE:
            domain = org.custom_domain
        if domain:
            cards.append(
                DnsRecordCard(
                    purpose="traffic",
                    type="CNAME",
                    name=domain,
                    host=_relative_host(domain, domain, zone),
                    value=target,
                    temporary=False,
                )
            )
    # Ownership card first during ownership; traffic first once it is what matters.
    if current in (STAGE_ROUTING, STAGE_ACTIVE):
        cards.sort(key=lambda card: 0 if card.purpose == "traffic" else 1)
    return cards


def status_for(org: Org, *, zone: str | None = None) -> DomainStatus:
    domain = org.pending_domain or org.custom_domain
    return DomainStatus(
        stage=stage(org),
        custom_domain=org.custom_domain,
        custom_domain_verified_at=org.custom_domain_verified_at,
        pending_domain=org.pending_domain,
        ownership_verified_at=org.pending_domain_ownership_verified_at,
        apex=_is_apex(domain, zone) if domain else None,
        records=record_cards(org, zone=zone),
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


# --------------------------------------------------------------------------- #
# Cloudflare seam (lazy imports — off unless the cloud posture is configured)
# --------------------------------------------------------------------------- #
def _cf_configured() -> bool:
    from app.core.cloud.cloudflare import cloudflare_configured

    return cloudflare_configured()


def _classify_cloudflare(exc: Exception, status: int | None) -> tuple[str, str]:
    """Map a Cloudflare failure to a safe diagnostic ``(code, state)`` (#292, #293).

    The token never appears in these messages (the client guarantees it); Cloudflare's own
    error text is safe to surface as the observation.
    """
    if status in (401, 403):
        return "cloudflare_auth", "failed"
    text = str(exc).lower()
    if any(word in text for word in ("entitle", "quota", "limit", "not allowed", "plan")):
        return "cloudflare_entitlement", "failed"
    return "cloudflare_unavailable", "pending"


# --------------------------------------------------------------------------- #
# Post-activation lifecycle (#291): the columns ``hosts.custom_domain_live`` reads
#
# The wizard ends at ``active`` — ownership proven, DNS pointing here, certificate issued.
# Whether the domain *stays* served is a separate question with its own state, owned by
# ``app.core.cloud.domain_health`` and its daily sweep. These helpers are how the wizard
# writes that state, so a "check now" here and the sweep can never disagree about whether a
# domain is live.
# --------------------------------------------------------------------------- #
def _reset_health(org: Org) -> None:
    """Forget every lifecycle observation.

    Two callers, one reason: a domain with no Cloudflare hostname has nothing to poll (there
    ``custom_domain_live`` treats verified as live), and a *cleared* domain must not leave the
    slug host rendering a health warning about a domain that no longer exists.
    """
    org.cf_hostname_status = None
    org.cf_ssl_status = None
    org.domain_dns_ok = None
    org.domain_cert_expires_at = None
    org.domain_checked_at = None
    org.domain_check_error = None
    org.domain_alerted_for = None


def _apply_hostname_health(org: Org, record: dict | None) -> str | None:
    """Write the Cloudflare-derived lifecycle columns from one custom-hostname record and
    return its error text. Deliberately mirrors ``domain_health.refresh_domain_health`` —
    same columns, same vocabulary — so the two writers stay interchangeable."""
    if record is None:
        org.cf_hostname_status = "deleted"
        org.cf_ssl_status = None
        org.domain_cert_expires_at = None
        return "custom hostname no longer exists on Cloudflare"
    from app.core.cloud.domain_health import parse_hostname_record

    health = parse_hostname_record(record)
    org.cf_hostname_status = health.hostname_status
    org.cf_ssl_status = health.ssl_status
    org.domain_cert_expires_at = health.cert_expires_at
    return health.error


def _seed_health(org: Org, record: dict | None, *, dns_ok: bool | None = None) -> None:
    """Seed the lifecycle state at activation from the record we already hold.

    Saves the extra Cloudflare round-trip a ``refresh_domain_health`` here would cost, and
    keeps ``custom_domain_live`` honest from the first moment: the wizard only activates once
    the hostname *and* its certificate report active, so the canonical host may switch
    straight away instead of waiting for the next sweep.
    """
    if record is None:
        _reset_health(org)
        return
    error = _apply_hostname_health(org, record)
    org.domain_check_error = error[:500] if error else None
    org.domain_dns_ok = dns_ok
    org.domain_checked_at = datetime.now(UTC)
    org.domain_alerted_for = None


async def _release_hostname(hostname_id: str | None, domain: str | None) -> None:
    """Best-effort removal — a leftover hostname routes nothing once the org row forgets it,
    and the next verification adopts it (find-then-create)."""
    if not hostname_id or not _cf_configured():
        return
    from app.core.cloud.cloudflare import CloudflareError, delete_custom_hostname

    try:
        await delete_custom_hostname(hostname_id)
    except CloudflareError:
        logger.exception("could not delete cloudflare custom hostname for %s", domain)


async def _sync_ingress(session: AsyncSession) -> None:
    if not settings.is_cloud:
        return
    from app.core.cloud.ingress import sync_ingress

    await sync_ingress(session)


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
async def claim(session: AsyncSession, actor, org: Org, raw_domain: str) -> Org:  # noqa: ANN001
    """Start (or restart) the wizard: reserve the name and issue a fresh ownership token.
    Any active custom domain keeps routing until the new one activates."""
    domain = normalize_domain(raw_domain)
    if await repo.domain_taken(session, domain, exclude_org_id=org.id):
        raise AppError("domain_taken", "errors.domain_taken", status_code=409)
    # A superseded in-flight claim releases its provisioned hostname — nothing routes it.
    if org.pending_cf_hostname_id and org.pending_domain != domain:
        await _release_hostname(org.pending_cf_hostname_id, org.pending_domain)
        org.pending_cf_hostname_id = None
    org.pending_domain = domain
    org.domain_verification_token = secrets.token_hex(16)
    org.pending_domain_ownership_verified_at = None
    await session.flush()
    await audit.record(
        session, actor=actor, action="domain.claim", org=org, detail={"domain": domain}
    )
    return org


async def cancel_claim(session: AsyncSession, actor, org: Org) -> Org:  # noqa: ANN001
    """Abandon the in-flight claim and **leave any live domain exactly as it is**.

    Distinct from :func:`clear` because the two are opposite intentions that the wizard's own
    copy already distinguishes: mid-replacement the button says *cancel setup*, and a customer
    who changes their mind about moving to a new domain must not thereby take their working
    production address down. Falls through to nothing when there is no claim.
    """
    pending = org.pending_domain
    hostname = org.pending_cf_hostname_id
    if pending is None:
        return org
    org.pending_domain = None
    org.domain_verification_token = None
    org.pending_domain_ownership_verified_at = None
    org.pending_cf_hostname_id = None
    await session.flush()
    await audit.record(
        session, actor=actor, action="domain.cancel_claim", org=org, detail={"domain": pending}
    )
    # The abandoned hostname routes nothing once the org forgets it; releasing it is best
    # effort, exactly as in clear().
    if hostname and hostname != org.cf_hostname_id:
        await _release_hostname(hostname, pending)
    return org


async def clear(session: AsyncSession, actor, org: Org) -> Org:  # noqa: ANN001
    """Drop the custom domain and any in-flight claim; the org keeps resolving via
    ``<slug>.<base_domain>``."""
    cleared = org.custom_domain or org.pending_domain
    active_hostname = org.cf_hostname_id
    pending_hostname = org.pending_cf_hostname_id
    org.custom_domain = None
    org.custom_domain_verified_at = None
    org.cf_hostname_id = None
    org.pending_domain = None
    org.domain_verification_token = None
    org.pending_domain_ownership_verified_at = None
    org.pending_cf_hostname_id = None
    _reset_health(org)
    await session.flush()
    await audit.record(
        session, actor=actor, action="domain.clear", org=org, detail={"domain": cleared}
    )
    await _release_hostname(active_hostname, cleared)
    if pending_hostname != active_hostname:
        await _release_hostname(pending_hostname, cleared)
    await _sync_ingress(session)
    return org


async def attach(
    session: AsyncSession,
    actor,  # noqa: ANN001
    org: Org,
    raw_domain: str,
    *,
    activate: bool = True,
) -> Org:
    """Operator-asserted domain configuration (instance console / provisioning API, #292).

    ``activate=True``: the operator vouches for ownership — the TXT challenge is skipped
    (recorded as such on the audit trail), the edge hostname is provisioned fail-closed, and
    the domain routes as soon as the customer's DNS points at the edge. ``activate=False``
    only pre-claims: the org's own admin resumes the wizard at the ownership step, exactly
    as if they had claimed it themselves.
    """
    domain = normalize_domain(raw_domain)
    if await repo.domain_taken(session, domain, exclude_org_id=org.id):
        raise AppError("domain_taken", "errors.domain_taken", status_code=409)
    if not activate:
        return await claim(session, actor, org, domain)

    record: dict | None = None
    if _cf_configured():
        from app.core.cloud.cloudflare import CloudflareError, ensure_custom_hostname_record

        try:
            record = await ensure_custom_hostname_record(domain)
        except CloudflareError as exc:
            code, _ = _classify_cloudflare(exc, exc.status)
            logger.warning("cloudflare attach failed for %s: %s", domain, exc)
            raise AppError(code, f"errors.{code}", status_code=502) from exc
    hostname_id = str(record["id"]) if record and record.get("id") else None
    previous_hostname = org.cf_hostname_id
    previous_domain = org.custom_domain
    org.custom_domain = domain
    org.custom_domain_verified_at = datetime.now(UTC)
    org.cf_hostname_id = hostname_id
    org.pending_domain = None
    org.domain_verification_token = None
    org.pending_domain_ownership_verified_at = None
    org.pending_cf_hostname_id = None
    # A freshly created hostname answers "pending": operator-asserted ownership routes the
    # domain, but it is not *live* until Cloudflare reports the certificate active, so the
    # canonical host stays the slug host until the sweep or a check says otherwise (#291).
    _seed_health(org, record)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="domain.attach",
        org=org,
        detail={"domain": domain, "ownership": "operator-asserted"},
    )
    if previous_hostname and previous_hostname != hostname_id:
        await _release_hostname(previous_hostname, previous_domain)
    await _sync_ingress(session)
    return org


async def _activate(  # noqa: ANN001
    session: AsyncSession, actor, org: Org, record: dict | None = None
) -> None:
    """Promote the pending domain: it starts routing and gets (or already has) its edge
    certificate. Re-checks global uniqueness — another org may have raced the same name.

    ``record`` is the custom-hostname record the routing checks just read; passing it seeds
    the lifecycle state (#291) without a second Cloudflare call. Its absence means there is no
    Cloudflare hostname for this domain (self-host, or the Traefik/Let's Encrypt posture),
    where verified *is* live and there is no state to keep.
    """
    if await repo.domain_taken(session, org.pending_domain, exclude_org_id=org.id):
        raise AppError("domain_taken", "errors.domain_taken", status_code=409)
    previous_hostname = org.cf_hostname_id
    previous_domain = org.custom_domain
    org.custom_domain = org.pending_domain
    org.custom_domain_verified_at = datetime.now(UTC)
    org.cf_hostname_id = org.pending_cf_hostname_id
    org.pending_domain = None
    org.domain_verification_token = None
    org.pending_domain_ownership_verified_at = None
    org.pending_cf_hostname_id = None
    # The wizard only reaches here with the traffic DNS observed, so dns_ok is a fact, not a
    # guess — which is what lets the canonical host switch on this same request (#291).
    _seed_health(org, record, dns_ok=True if record is not None else None)
    await session.flush()
    await audit.record(
        session,
        actor=actor,
        action="domain.activate",
        org=org,
        detail={"domain": org.custom_domain},
    )
    if previous_hostname and previous_hostname != org.cf_hostname_id:
        await _release_hostname(previous_hostname, previous_domain)
    await _sync_ingress(session)


# --------------------------------------------------------------------------- #
# The check machine
# --------------------------------------------------------------------------- #
async def _check_ownership(org: Org) -> DomainCheck:
    name = f"{CHALLENGE_PREFIX}.{org.pending_domain}"
    expected = org.domain_verification_token
    result = await dnscheck.txt(name)
    if result.error == dnscheck.NXDOMAIN:
        return _check("ownership", "pending", "txt_nxdomain", expected=expected)
    if result.error == dnscheck.TIMEOUT:
        return _check("ownership", "pending", "dns_timeout", expected=expected)
    if result.error == dnscheck.SERVFAIL:
        return _check("ownership", "failed", "dns_servfail", expected=expected)
    if expected in result.values:
        return _check("ownership", "ok", "ownership_ok", expected=expected, observed=expected)
    if not result.values:
        return _check("ownership", "pending", "txt_missing", expected=expected)
    observed = "; ".join(value[:80] for value in result.values[:5])
    return _check("ownership", "failed", "txt_wrong_value", expected=expected, observed=observed)


class _DnsEvidence(NamedTuple):
    """What DNS alone can say about the traffic record, and the raw material behind it.

    Only ``check.state == "ok"`` is proof. Everything else is circumstantial, and *how*
    circumstantial depends on ``cname_seen``: a CNAME that is visible and points elsewhere is
    an observed wrong value, while addresses that merely fail to match are what every proxied
    domain in the world looks like. :func:`routing_check` is the only caller allowed to turn
    any of this into a state.
    """

    check: DomainCheck
    addresses: list[str]
    cname_seen: bool


async def _dns_evidence(domain: str, target: str) -> _DnsEvidence:
    result = await dnscheck.cname(domain)
    if result.error == dnscheck.TIMEOUT:
        return _DnsEvidence(
            _check("dns_target", "pending", "dns_timeout", expected=target), [], False
        )
    if result.error == dnscheck.SERVFAIL:
        return _DnsEvidence(
            _check("dns_target", "failed", "dns_servfail", expected=target), [], False
        )
    if result.error == dnscheck.NXDOMAIN:
        return _DnsEvidence(
            _check("dns_target", "pending", "target_nxdomain", expected=target), [], False
        )
    if target in result.values:
        return _DnsEvidence(
            _check("dns_target", "ok", "target_ok", expected=target, observed=target), [], True
        )
    cname_values = list(result.values)
    # No matching CNAME. Either the record points elsewhere, or there is no CNAME to see at
    # all — an apex cannot carry one, and a proxying CDN answers with its own addresses
    # instead. Compare what the two names actually resolve to; matching addresses mean the
    # name effectively points here.
    observed_a, target_a = await dnscheck.a_records(domain), await dnscheck.a_records(target)
    if observed_a.values and set(observed_a.values) & set(target_a.values):
        return _DnsEvidence(
            _check("dns_target", "ok", "target_ok", expected=target, observed=target),
            observed_a.values,
            bool(cname_values),
        )
    observed = ", ".join((cname_values or observed_a.values)[:4])
    if cname_values or observed_a.values:
        return _DnsEvidence(
            _check("dns_target", "failed", "target_wrong", expected=target, observed=observed),
            observed_a.values,
            bool(cname_values),
        )
    return _DnsEvidence(
        _check("dns_target", "pending", "target_missing", expected=target), [], False
    )


async def routing_check(
    domain: str,
    target: str,
    *,
    slug: str | None = None,
    edge_ok: bool | None = None,
) -> DomainCheck:
    """Does traffic for ``domain`` reach this instance? The one answer both callers use.

    The wizard's "check now" and the unattended sweep ask the same question, so they run the
    same function — the two implementations that preceded this could and did disagree, and a
    customer told "your domain is fine" while the sweep mailed them an outage is the worst of
    both.

    **Addresses are the weakest evidence available, so they are asked last and never decide
    alone.** A domain fronted by the customer's own Cloudflare — the supported orange-to-orange
    setup — publishes anycast addresses from *their* zone; comparing them against the edge
    hostname's own anycast addresses can only ever mismatch, no matter how correctly the
    domain is configured. In order of strength:

    1. an explicit CNAME to the edge, or addresses that match it — DNS proves it outright;
    2. a fetch of the domain that this instance answers for this org
       (:mod:`app.core.domainprobe`) — proof through any proxy, any CDN, any apex flattening;
    3. a fetch that something *else* answered — proof of the opposite;
    4. the edge network reporting the hostname active — Cloudflare would not, if the
       customer's DNS had stopped reaching it;
    5. nothing conclusive — ``pending``, never ``failed``. A domain that is serving must not
       be demoted because we could not confirm it; the states above are what demote it.
    """
    dns, addresses, cname_seen = await _dns_evidence(domain, target)
    if dns.state == "ok" or dns.code == "dns_timeout":
        return dns
    verdict = domainprobe.UNKNOWN
    # Fetch only when the name resolves to *something* that is not us. A name that resolves to
    # nothing yet (the wizard's normal state while a record propagates, and its most-polled
    # one) has nobody to answer, and a zone answering SERVFAIL serves nobody either — spending
    # a connection timeout on each poll to learn that would be the slowest possible no.
    if slug and dns.code == "target_wrong":
        verdict = await domainprobe.probe(domain, slug, addresses=addresses or None)
    if verdict == domainprobe.OURS:
        return _check(
            "dns_target", "ok", "target_proxied", expected=target, observed=dns.observed
        )
    if verdict == domainprobe.OTHER:
        return _check(
            "dns_target", "failed", "target_wrong", expected=target, observed=dns.observed
        )
    if edge_ok:
        return _check(
            "dns_target", "ok", "target_edge_confirmed", expected=target, observed=dns.observed
        )
    if dns.code == "target_wrong" and not cname_seen:
        # Addresses that do not match, and no CNAME to explain them: that is what *every*
        # proxied domain looks like, and it establishes nothing. Reads pending, demotes
        # nothing. Two neighbours are deliberately left alone — a CNAME that is visible and
        # points elsewhere is an observed wrong value (the wizard must still say *where* it
        # went, #292), and a zone that SERVFAILs serves nobody either way.
        return _check(
            "dns_target", "pending", "target_unconfirmed", expected=target, observed=dns.observed
        )
    return dns


def dns_verdict(check: DomainCheck) -> bool | None:
    """The tri-state ``orgs.domain_dns_ok`` column for one routing check. ``None`` — no
    verdict — is a first-class outcome: it must never be recorded as "the customer moved"."""
    return {"ok": True, "failed": False}.get(check.state)


def _hostname_checks(record: dict | None) -> list[DomainCheck]:
    """Map one Cloudflare custom-hostname record to hostname + certificate checks."""
    if record is None:
        return [_check("hostname", "failed", "hostname_deleted")]
    checks: list[DomainCheck] = []
    hostname_status = str(record.get("status") or "pending")
    if hostname_status == "active":
        checks.append(_check("hostname", "ok", "hostname_ok", observed=hostname_status))
    elif hostname_status in ("moved", "blocked", "deleted"):
        checks.append(
            _check("hostname", "failed", f"hostname_{hostname_status}", observed=hostname_status)
        )
    else:
        verification = "; ".join(
            str(err)[:120] for err in (record.get("verification_errors") or [])[:3]
        )
        checks.append(
            _check(
                "hostname",
                "pending",
                "hostname_pending",
                observed=verification or hostname_status,
            )
        )
    ssl = record.get("ssl") or {}
    ssl_status = str(ssl.get("status") or "initializing")
    validation = "; ".join(
        str(err.get("message", err))[:160] for err in (ssl.get("validation_errors") or [])[:3]
    )
    if ssl_status == "active":
        checks.append(_check("certificate", "ok", "cert_ok", observed=ssl_status))
    elif "expired" in ssl_status or "timed_out" in ssl_status or "deleted" in ssl_status:
        checks.append(
            _check("certificate", "failed", "cert_failed", observed=validation or ssl_status)
        )
    elif validation:
        # Pending with concrete validation errors: actionable now, not just "wait".
        checks.append(
            _check(
                "certificate",
                "failed",
                "cert_failed",
                observed=f"{ssl_status}: {validation}",
            )
        )
    else:
        checks.append(_check("certificate", "pending", "cert_pending", observed=ssl_status))
    return checks


async def _routing_checks(org: Org) -> tuple[list[DomainCheck], bool, dict | None]:
    """The independent routing/certificate states (#292), whether all are go, and the
    custom-hostname record behind them — which activation seeds its lifecycle state from.

    The edge is asked **before** the routing check, because the routing check takes the edge's
    verdict as one of its signals: a proxied domain publishes nobody's addresses but its
    proxy's, and Cloudflare reporting the hostname active is what says the customer's DNS
    still reaches it. The checks are returned in display order regardless.
    """
    edge_checks: list[DomainCheck] = []
    edge_ok = True
    record: dict | None = None
    if _cf_configured():
        from app.core.cloud.cloudflare import CloudflareError, get_custom_hostname

        edge_ok = False
        try:
            if org.pending_cf_hostname_id is None:
                from app.core.cloud.cloudflare import ensure_custom_hostname

                org.pending_cf_hostname_id = await ensure_custom_hostname(org.pending_domain)
            record = await get_custom_hostname(org.pending_cf_hostname_id)
            if record is None:
                # Deleted behind our back: forget the id so the next check re-provisions.
                org.pending_cf_hostname_id = None
                edge_checks.append(_check("hostname", "failed", "hostname_deleted"))
            else:
                edge_checks = _hostname_checks(record)
                edge_ok = all(item.state == "ok" for item in edge_checks)
        except CloudflareError as exc:
            code, state = _classify_cloudflare(exc, exc.status)
            edge_checks.append(_check("hostname", state, code, observed=str(exc)[:200]))

    checks: list[DomainCheck] = []
    target = _cname_target()
    target_ok = True
    if target is not None:
        # No slug is passed while the domain is still pending: it does not resolve to this org
        # yet (``resolve_org`` requires a *verified* domain), so a probe could only ever answer
        # "unknown". Here the edge's own verdict carries the proxied case.
        target_check = await routing_check(org.pending_domain, target, edge_ok=edge_ok)
        checks.append(target_check)
        target_ok = target_check.state == "ok"
    checks.extend(edge_checks)
    return checks, target_ok and edge_ok, record


async def run_checks(session: AsyncSession, actor, org: Org) -> DomainCheckReport:  # noqa: ANN001
    """Probe the current stage's conditions, advance every transition they satisfy, and
    report each layer separately. Safe to call repeatedly — it is the wizard's poll."""
    correlation_id = uuid.uuid4().hex[:12]
    checked_at = datetime.now(UTC)
    checks: list[DomainCheck] = []
    advanced = False

    domain = org.pending_domain or org.custom_domain
    zone: str | None = None
    provider_key: str | None = None
    provider_name: str | None = None
    if domain:
        zone, nameservers = await dnscheck.ns_zone(domain)
        joined = " ".join(nameservers).lower()
        for needle, key, name in _PROVIDERS:
            if needle in joined:
                provider_key, provider_name = key, name
                break

    if stage(org) == STAGE_OWNERSHIP:
        ownership = await _check_ownership(org)
        checks.append(ownership)
        if ownership.state == "ok":
            org.pending_domain_ownership_verified_at = checked_at
            await session.flush()
            await audit.record(
                session,
                actor=actor,
                action="domain.ownership_verified",
                org=org,
                detail={"domain": org.pending_domain},
            )
            advanced = True
            if not settings.is_cloud:
                # Self-host: routing is the operator's own ingress — ownership is the whole
                # story, exactly as before #292.
                await _activate(session, actor, org)

    if stage(org) == STAGE_ROUTING:
        routing_checks, ready, record = await _routing_checks(org)
        checks.extend(routing_checks)
        await session.flush()
        if ready:
            await _activate(session, actor, org, record)
            advanced = True

    if stage(org) == STAGE_ACTIVE and org.custom_domain and not org.pending_domain:
        # Monitoring for an active domain: the same independent states — and this is also the
        # "check now" that writes the lifecycle columns ``hosts.custom_domain_live`` reads
        # (#291). One reconciliation feeds both, so the diagnostics the customer sees and the
        # canonical-host decision can never disagree; the daily sweep runs the identical one
        # unattended (``domain_health.refresh_domain_health``), down to ``routing_check``.
        edge_checks: list[DomainCheck] = []
        edge_ok: bool | None = None
        if _cf_configured() and org.cf_hostname_id:
            from app.core.cloud.cloudflare import CloudflareError, get_custom_hostname

            try:
                record = await get_custom_hostname(org.cf_hostname_id)
                edge_checks = _hostname_checks(record)
                edge_ok = all(item.state == "ok" for item in edge_checks)
                error = _apply_hostname_health(org, record)
            except CloudflareError as exc:
                code, state = _classify_cloudflare(exc, exc.status)
                edge_checks.append(_check("hostname", state, code, observed=str(exc)[:200]))
                # An API blip is not a state change: keep the last known statuses (the sweep
                # takes the same position) and record what went wrong instead.
                error = str(exc)
            org.domain_check_error = error[:500] if error else None
            org.domain_checked_at = checked_at
        target = _cname_target()
        if target is not None:
            target_check = await routing_check(
                org.custom_domain, target, slug=org.slug, edge_ok=edge_ok
            )
            checks.append(target_check)
            # Tri-state: a check that could not establish an answer must never be recorded as
            # "the customer moved their DNS away".
            org.domain_dns_ok = dns_verdict(target_check)
            org.domain_checked_at = checked_at
        checks.extend(edge_checks)
        await session.flush()

    failed = [item.code for item in checks if item.state != "ok"]
    if failed:
        logger.info(
            "domain check %s org=%s domain=%s stage=%s results=%s",
            correlation_id,
            org.slug,
            domain,
            stage(org),
            ",".join(f"{item.key}:{item.code}" for item in checks),
        )
    return DomainCheckReport(
        status=status_for(org, zone=zone),
        checked_at=checked_at,
        correlation_id=correlation_id,
        provider=provider_key,
        provider_name=provider_name,
        zone=zone,
        advanced=advanced,
        checks=checks,
    )
