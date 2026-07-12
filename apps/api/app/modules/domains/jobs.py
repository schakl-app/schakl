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

from app.core.jobs import run_per_org
from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org
from app.modules.domains.dns import fetch_dns
from app.modules.domains.models import Domain

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


async def refresh_all_domains(ctx: dict) -> None:
    """ARQ entrypoint: refresh DNS facts for every active org's domains (#92)."""
    await run_per_org(_refresh_org)


async def refresh_domain_dns(ctx: dict, org_id: str, domain_id: str) -> None:
    """One-off first fetch after create (#125), enqueued by ``DomainService.create``.

    Binds the org like ``run_per_org`` does (RLS GUC per tenant). A missing org or row is a
    quiet no-op — the create's transaction may have rolled back after the enqueue, or the
    domain may already be deleted; neither is this job's problem.
    """
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
