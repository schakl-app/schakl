"""Scheduled + one-off public-DNS refresh for domains (issues #92, #125).

The daily ARQ cron fans out per org via ``run_per_org`` (RLS GUC bound per tenant, one
transaction each), re-querying nameservers + DNSSEC + MX so the domain page shows fresh data
without anyone pressing refresh. ``refresh_domain_dns`` is the one-off variant the API enqueues
right after a domain is created, so a new domain never sits on "never checked" until the nightly
run. Both run in the worker, never in a request; each lookup fails soft (see ``dns.fetch_dns``).
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import license_state
from app.core.events import SystemContext, emit
from app.core.jobs import run_per_org
from app.core.models import Org, OrgSettings, OrgStatus
from app.core.timezone import org_zoneinfo
from app.db import async_session_maker, set_current_org
from app.modules.domains.dns import fetch_dns
from app.modules.domains.models import BILLABLE_STATUSES, Domain, DomainTldPrice
from app.modules.domains.service import add_months

logger = logging.getLogger("schakl.domains")


async def _refresh_org(org: Org, session: AsyncSession) -> None:
    domains = (
        (await session.execute(select(Domain).where(Domain.org_id == org.id))).scalars().all()
    )
    for domain in domains:
        facts = await fetch_dns(domain.name)
        domain.nameservers = facts.nameservers
        domain.dnssec = facts.dnssec
        domain.mx_records = facts.mx
        domain.dns_checked_at = datetime.now(UTC)
    if domains:
        logger.info("refreshed DNS for %s domains in org %s", len(domains), org.slug)


async def _licensed() -> bool:
    """Whether the ``domains`` sku is still writable (issue #137): the mount-time 402 gate
    covers requests, but crons write on a schedule — an expired license must stop the
    background refresh too (expired = read-only, not gone; stored DNS facts stay visible)."""
    return (await license_state()).writable("domains")


async def refresh_all_domains(ctx: dict) -> None:
    """ARQ entrypoint: refresh DNS facts for every active org's domains (#92)."""
    if not await _licensed():
        return
    await run_per_org(_refresh_org)


async def _advance_renewals_org(org: Org, session: AsyncSession) -> None:
    """Fire ``domain.due`` for every renewal that has come up and roll it a year forward.

    The resolved amount is the ``price_override``, else the TLD's price *at the invoice
    date* — history answers, current state never reprices (#250). A due domain with no
    resolvable price is left untouched: once the org prices its TLD, the next run bills
    from the original due date, nothing is silently skipped or back-filled.
    """
    today = datetime.now(await org_zoneinfo(session, org.id)).date()
    due = (
        (
            await session.execute(
                select(Domain).where(
                    Domain.org_id == org.id,
                    Domain.status.in_(BILLABLE_STATUSES),
                    Domain.next_invoice_date.is_not(None),
                    Domain.next_invoice_date <= today,
                )
            )
        )
        .scalars()
        .all()
    )
    if not due:
        return
    org_currency = (
        await session.scalar(
            select(OrgSettings.currency).where(OrgSettings.org_id == org.id)
        )
        or "EUR"
    )
    ctx = SystemContext(org=org, session=session)
    advanced = 0
    for domain in due:
        invoice_date = domain.next_invoice_date
        price_row = None
        if domain.tld:
            price_row = await session.scalar(
                select(DomainTldPrice)
                .where(
                    DomainTldPrice.org_id == org.id,
                    DomainTldPrice.tld == domain.tld,
                    DomainTldPrice.valid_from <= invoice_date,
                )
                .order_by(DomainTldPrice.valid_from.desc())
                .limit(1)
            )
        if domain.price_override is not None:
            amount = domain.price_override
            currency = price_row.currency if price_row is not None else org_currency
        elif price_row is not None:
            amount = price_row.amount
            currency = price_row.currency
        else:
            continue
        await emit(
            "domain.due",
            ctx,
            {
                "domain_id": domain.id,
                "company_id": domain.company_id,
                "name": domain.name,
                "tld": domain.tld,
                "amount": str(amount),
                "currency": currency,
                "period_start": add_months(invoice_date, -12).isoformat(),
                "period_end": invoice_date.isoformat(),
            },
        )
        domain.next_invoice_date = add_months(invoice_date, 12)
        advanced += 1
    if advanced:
        logger.info("advanced %s due domain renewals in org %s", advanced, org.slug)


async def advance_domain_renewals(ctx: dict) -> None:
    """ARQ entrypoint: fire ``domain.due`` and roll the renewal cycle forward, per org."""
    if not await _licensed():
        return
    await run_per_org(_advance_renewals_org)


async def refresh_domain_dns(ctx: dict, org_id: str, domain_id: str) -> None:
    """One-off first fetch after create (#125), enqueued by ``DomainService.create``.

    Binds the org like ``run_per_org`` does (RLS GUC per tenant). A missing org or row is a
    quiet no-op — the create's transaction may have rolled back after the enqueue, or the
    domain may already be deleted; neither is this job's problem.
    """
    if not await _licensed():
        return
    async with async_session_maker() as session:
        org = (
            await session.execute(
                select(Org).where(
                    Org.id == uuid.UUID(org_id), Org.status == OrgStatus.ACTIVE.value
                )
            )
        ).scalar_one_or_none()
        if org is None:
            return
        await set_current_org(session, org.id)
        domain = (
            await session.execute(
                select(Domain).where(
                    Domain.org_id == org.id, Domain.id == uuid.UUID(domain_id)
                )
            )
        ).scalar_one_or_none()
        if domain is None:
            return
        facts = await fetch_dns(domain.name)
        domain.nameservers = facts.nameservers
        domain.dnssec = facts.dnssec
        domain.mx_records = facts.mx
        domain.dns_checked_at = datetime.now(UTC)
        await session.commit()
