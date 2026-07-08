"""Tenant context for background jobs (CLAUDE.md §5).

``require_context`` is request-scoped; worker jobs (ARQ cron) have no request. This helper
gives them the same tenant discipline: run a callback once per org with the RLS GUC bound to
that org, committing per org so one tenant's failure can't poison another's work. ``orgs``
itself has no RLS (it is the tenant table), so listing it from a job is safe.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Org
from app.db import async_session_maker, set_current_org

logger = logging.getLogger("vlotr.jobs")

PerOrgCallback = Callable[[Org, AsyncSession], Awaitable[None]]


async def run_per_org(callback: PerOrgCallback) -> None:
    """Invoke ``callback(org, session)`` for every org, each in its own transaction.

    A failure is rolled back and logged for that org only; the remaining orgs still run.
    """
    async with async_session_maker() as session:
        orgs = (await session.execute(select(Org))).scalars().all()

    for org in orgs:
        async with async_session_maker() as session:
            await set_current_org(session, org.id)
            try:
                await callback(org, session)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("per-org job failed for org %s", org.slug)
