"""Custom-domain certificate lifecycle (#291). Business-licensed — see this directory's LICENSE.

Creating a Cloudflare custom hostname is not activation: the hostname must reach status
``active``, its DV certificate must reach SSL status ``active``, and the customer's DNS must
keep pointing at the SaaS target — and certificate *renewal* silently depends on all three
staying true. This module owns that state:

- :func:`refresh_domain_health` — one reconciliation: fetch the custom hostname from
  Cloudflare, run the shared routing check (``domainflow.routing_check``), write the result
  onto the org row. Called from the verify flow (seed), the settings page's "check now"
  endpoint, and the sweep. It returns the per-layer :class:`DomainDiagnosis` behind the
  verdict it wrote, which is what lets the alert mail name the record that is wrong.
- :func:`sweep_domain_health` — the daily safety sweep over every org with a Cloudflare
  custom hostname. It alerts the org's **administrators** — the people who can actually change
  the setting, never a client login — **once per distinct problem**
  (``orgs.domain_alerted_for`` fingerprint) when the domain stops being live, and ahead of a
  certificate expiry that automatic HTTP DCV renewal evidently is not solving — renewal
  failure must be discovered here, not by browsers rejecting TLS. The fingerprint is recorded
  only when a mail actually went out: a problem nobody was told about must stay unreported,
  not be silently marked as handled.

What is *not* here: Delegated DCV. Deliberately deferred — exact, non-wildcard hostnames
renew through automatic HTTP DCV as long as the hostname stays active and keeps pointing at
the SaaS target, which is precisely what this module monitors. See docs/CLOUD.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core import domainflow
from app.core.cloud import cloudflare as cf
from app.core.cloud.domain_alert import compose_domain_alert
from app.core.cloud.ingress import cname_target
from app.core.domainflow import DomainCheck
from app.core.email.branding import DEFAULT_PRIMARY
from app.core.email.service import send_org_email
from app.core.hosts import custom_domain_live
from app.core.models import Membership, Org, OrgSettings, OrgStatus
from app.db import set_current_org

logger = logging.getLogger(__name__)

#: Cloudflare renews DV certificates ~30 days before expiry; one this close means every
#: renewal attempt has been failing for weeks and someone has to act.
EXPIRY_ALERT_DAYS = 15


@dataclass(frozen=True)
class DomainDiagnosis:
    """The evidence behind one reconciliation, per layer — DNS routing, edge hostname, TLS.

    ``refresh_domain_health`` writes the *verdict* onto the org row (that is what
    ``custom_domain_live`` reads); this carries the *reasoning* — which layer, what was
    expected, what was observed — so the alert mail can name the wrong record instead of
    telling an admin to go and look. Same objects the settings screen renders, so the mail
    and the screen cannot phrase the same problem differently.
    """

    checks: tuple[DomainCheck, ...] = ()


@dataclass(frozen=True)
class HostnameHealth:
    """The lifecycle-relevant slice of a Cloudflare custom-hostname record."""

    hostname_status: str | None
    ssl_status: str | None
    cert_expires_at: datetime | None
    error: str | None


def _parse_when(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_hostname_record(record: dict[str, Any]) -> HostnameHealth:
    """Read status, SSL status, certificate expiry and the first validation error.

    Defensive throughout: the record shape follows Cloudflare's API and the SDK-less client
    returns it verbatim, so every field is optional here.
    """
    ssl = record.get("ssl") or {}
    errors: list[str] = []
    for err in record.get("verification_errors") or []:
        errors.append(str(err.get("message", err)) if isinstance(err, dict) else str(err))
    for err in ssl.get("validation_errors") or []:
        errors.append(str(err.get("message", err)) if isinstance(err, dict) else str(err))
    expires = _parse_when(ssl.get("expires_on"))
    for certificate in ssl.get("certificates") or []:
        candidate = _parse_when((certificate or {}).get("expires_on"))
        if candidate and (expires is None or candidate > expires):
            expires = candidate
    return HostnameHealth(
        hostname_status=str(record["status"]) if record.get("status") else None,
        ssl_status=str(ssl["status"]) if ssl.get("status") else None,
        cert_expires_at=expires,
        error="; ".join(errors) or None,
    )


async def refresh_domain_health(org: Org) -> DomainDiagnosis:
    """One reconciliation for one org: Cloudflare state + the routing check → the org row.

    Mutates the loaded ORM object only (no queries), so a request handler may run it inside
    ``ctx.release_db()`` — the network calls here (Cloudflare, DNS, and the routing check's own
    fetch of the domain) are exactly what that seam exists for.
    A Cloudflare API failure keeps the previous statuses (an API blip is not a state change)
    and records the error text instead.

    Returns the per-layer checks it reached the verdict with. They cost nothing extra — the
    lookups already happened — and they are the difference between an alert that says "your
    domain is broken" and one that says which record points where.
    """
    error: str | None = None
    edge_ok: bool | None = None
    edge_checks: list[DomainCheck] = []
    routing: DomainCheck | None = None
    if org.cf_hostname_id and cf.cloudflare_configured():
        try:
            record = await cf.get_custom_hostname(org.cf_hostname_id)
            if record is None and org.custom_domain:
                # Deleted on Cloudflare's side; re-adopt by name if someone recreated it.
                record = await cf.find_custom_hostname(org.custom_domain)
                if record and record.get("id"):
                    org.cf_hostname_id = str(record["id"])
            if record is None:
                org.cf_hostname_status = "deleted"
                org.cf_ssl_status = None
                org.domain_cert_expires_at = None
                error = "custom hostname no longer exists on Cloudflare"
            else:
                health = parse_hostname_record(record)
                org.cf_hostname_status = health.hostname_status
                org.cf_ssl_status = health.ssl_status
                org.domain_cert_expires_at = health.cert_expires_at
                edge_ok = health.hostname_status == "active" and health.ssl_status == "active"
                error = health.error
            edge_checks = domainflow.hostname_checks(record)
        except cf.CloudflareError as exc:
            # No edge verdict at all this round: the previous statuses stand and the error
            # text below is what the mail reports. Inventing a failed check here would
            # alert on our own outage.
            error = str(exc)
    if org.custom_domain:
        # The same function the wizard's "check now" runs, with the same signals in the same
        # order — one question, one answer. Two implementations of "does it still point here"
        # is how a customer ends up reading "your domain is fine" on a page while this sweep
        # mails them that it is not.
        routing = await domainflow.routing_check(
            org.custom_domain, cname_target(), slug=org.slug, edge_ok=edge_ok
        )
        org.domain_dns_ok = domainflow.dns_verdict(routing)
    org.domain_checked_at = datetime.now(UTC)
    org.domain_check_error = error[:500] if error else None
    # Display order, as on the settings screen: routing first, then the edge's own layers.
    return DomainDiagnosis(checks=tuple(([routing] if routing else []) + edge_checks))


def _alert_fingerprint(org: Org) -> str | None:
    """The state worth telling the org about, or None while everything is fine.

    A stable string per distinct problem: the sweep mails when the fingerprint *changes*,
    so a domain stuck in ``pending_validation`` is reported once, not daily — and a new,
    different problem is reported again.
    """
    if not custom_domain_live(org):
        dns_word = (
            "ok" if org.domain_dns_ok else "moved" if org.domain_dns_ok is False else "unknown"
        )
        return f"unhealthy:{org.cf_hostname_status}:{org.cf_ssl_status}:{dns_word}"
    expires = org.domain_cert_expires_at
    if expires and expires - datetime.now(UTC) < timedelta(days=EXPIRY_ALERT_DAYS):
        return f"expiry:{expires.date().isoformat()}"
    return None


async def _admin_emails(session: AsyncSession, org: Org) -> list[str]:
    """The org's administrators: active **staff** whose roles grant the domain setting.

    Two narrowings, and the alert needs both. *Can they fix it* — only a holder of
    ``settings.domain.write`` (or the owner's wildcard) can change a thing here, so nobody
    else is told about infrastructure they cannot touch. And *are they ours* — an external
    login is never a recipient: a client-role membership or a contact-linked portal account
    (#274 treats both as external) must not receive the agency's own operational mail, even
    if a misconfigured role handed it the permission. The seeded roles already land on
    owner + admin; the permission is checked rather than the role key so a tenant who
    delegates the domain to their own technical role still gets warned.
    """
    from app.core.auth.models import User
    from app.core.permissions.catalog import ROLE_CLIENT
    from app.core.permissions.models import MembershipRole, Role, RolePermission
    from app.core.portal import portal_user_ids

    await set_current_org(session, org.id)
    external_roles = (
        select(MembershipRole.membership_id)
        .join(Role, Role.id == MembershipRole.role_id)
        .where(MembershipRole.org_id == org.id, Role.key == ROLE_CLIENT)
    )
    rows = await session.execute(
        select(User.id, User.email)
        .join(Membership, Membership.user_id == User.id)
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
        .where(
            Membership.org_id == org.id,
            User.is_active.is_(True),
            RolePermission.permission.in_(["*", "settings.domain.write"]),
            Membership.id.not_in(external_roles),
        )
    )
    candidates = {user_id: email for user_id, email in rows if email}
    portal = await portal_user_ids(session, org.id, set(candidates))
    return sorted({email for user_id, email in candidates.items() if user_id not in portal})


async def _notify(
    session: AsyncSession, org: Org, fingerprint: str, diagnosis: DomainDiagnosis
) -> bool:
    """Mail the org's administrators; report whether any of them was actually reached.

    Best effort — a mail outage must not stall the sweep — but *not* silently: the sweep only
    remembers a problem it managed to tell someone about, so an org with no administrator
    holding the permission, or with no working transport, is alerted again tomorrow instead of
    having its outage marked as handled.
    """
    try:
        await set_current_org(session, org.id)
        row = await session.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id))
        recipients = await _admin_emails(session, org)
        if not recipients:
            logger.warning(
                "custom domain %s (org %s) needs attention but no administrator holds "
                "settings.domain.write",
                org.custom_domain,
                org.slug,
            )
            return False
        message = compose_domain_alert(
            org,
            kind="expiry" if fingerprint.startswith("expiry:") else "unhealthy",
            checks=diagnosis.checks,
            locale=(row.default_locale if row else None) or settings.default_locale,
            # Golden Rule 4: the displayed brand, never the internal org name (docs/EMAIL.md).
            brand=(row.brand_name if row else org.name),
            primary_color=(row.primary_color if row else None) or DEFAULT_PRIMARY,
            recipients=recipients,
        )
        sent = False
        for address in recipients:
            ok, _error = await send_org_email(session, org.id, replace(message, to=address))
            sent = sent or ok
        return sent
    except Exception:  # noqa: BLE001 — notification is best effort by contract
        logger.exception("domain health notification failed for org %s", org.slug)
        return False


async def sweep_domain_health(session: AsyncSession) -> dict[str, int]:
    """Reconcile every org with a Cloudflare-managed custom domain; alert on new problems.

    Only orgs with a ``cf_hostname_id`` are looked at: a Traefik/Let's Encrypt domain has no
    Cloudflare state, and its certificate renews with its router (#202).
    """
    if not cf.cloudflare_configured():
        return {"checked": 0, "alerted": 0}
    orgs = (
        await session.execute(
            select(Org).where(
                Org.custom_domain.is_not(None),
                Org.cf_hostname_id.is_not(None),
                Org.status != OrgStatus.DELETED.value,
            )
        )
    ).scalars().all()
    checked = alerted = 0
    for org in orgs:
        diagnosis = await refresh_domain_health(org)
        checked += 1
        fingerprint = _alert_fingerprint(org)
        if fingerprint is None:
            org.domain_alerted_for = None
            continue
        if fingerprint != org.domain_alerted_for:
            logger.warning(
                "custom domain %s (org %s) needs attention: %s",
                org.custom_domain, org.slug, fingerprint,
            )
            if await _notify(session, org, fingerprint, diagnosis):
                org.domain_alerted_for = fingerprint
                alerted += 1
    return {"checked": checked, "alerted": alerted}
