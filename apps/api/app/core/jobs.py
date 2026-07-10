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

from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org

logger = logging.getLogger("schakl.jobs")

PerOrgCallback = Callable[[Org, AsyncSession], Awaitable[None]]


async def run_per_org(callback: PerOrgCallback) -> None:
    """Invoke ``callback(org, session)`` for every **active** org, each in its own transaction.

    Suspended and soft-deleted orgs are skipped: a tenant that can't serve requests must not
    keep accruing background work either (issue #26). A failure is rolled back and logged for
    that org only; the remaining orgs still run.
    """
    async with async_session_maker() as session:
        orgs = (
            (await session.execute(select(Org).where(Org.status == OrgStatus.ACTIVE.value)))
            .scalars()
            .all()
        )

    for org in orgs:
        async with async_session_maker() as session:
            await set_current_org(session, org.id)
            try:
                await callback(org, session)
                await session.commit()
            except Exception:
                await session.rollback()
                logger.exception("per-org job failed for org %s", org.slug)
