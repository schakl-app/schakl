"""Tenant context for background jobs (CLAUDE.md §5).

``require_context`` is request-scoped; worker jobs (ARQ cron) have no request. This helper
gives them the same tenant discipline: run a callback once per org with the RLS GUC bound to
that org, committing per org so one tenant's failure can't poison another's work. ``orgs``
itself has no RLS (it is the tenant table), so listing it from a job is safe.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.models import Org, OrgStatus
from app.core.permissions.permset import PermissionSet
from app.core.roles import Role
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org

logger = logging.getLogger("schakl.jobs")

PerOrgCallback = Callable[[Org, AsyncSession], Awaitable[None]]


def system_context(org: Org, session: AsyncSession) -> RequestContext:
    """A :class:`RequestContext` for background work — the system acting inside one org.

    Lets a cron job reuse the same tenant-scoped services the request path uses (repos bind to
    ``org.id``; ``run_per_org`` has already set the RLS GUC) instead of forking a second copy of
    the business logic. The user is a transient placeholder that exists in no table: it matches
    no membership, so any service path that resolves *a person* 404s rather than inventing one —
    a job acts on explicit user ids, never as "someone". Wildcard permissions, because the
    permission system models people; what a *job* may do is decided by what the job calls.
    """
    return RequestContext(
        user=User(id=uuid.uuid4(), email="system@localhost", hashed_password="", is_active=True),
        org=org,
        role=Role.OWNER,
        session=session,
        permissions=PermissionSet.of(("*",)),
    )


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
