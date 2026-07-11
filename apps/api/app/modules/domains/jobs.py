"""Scheduled public-DNS refresh for every domain (issue #92).

An ARQ cron fans out per org via ``run_per_org`` (RLS GUC bound per tenant, one transaction each),
re-querying nameservers + DNSSEC so the domain page shows fresh data without anyone pressing
refresh. Runs in the worker, never in a request; each lookup fails soft (see ``dns.fetch_dns``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org
from app.core.models import Org
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
        domain.dns_checked_at = datetime.now(UTC)
    if domains:
        logger.info("refreshed DNS for %s domains in org %s", len(domains), org.slug)


async def refresh_all_domains(ctx: dict) -> None:
    """ARQ entrypoint: refresh nameservers + DNSSEC for every active org's domains (#92)."""
    await run_per_org(_refresh_org)
