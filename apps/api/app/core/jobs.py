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
from typing import Any

from arq.connections import ArqRedis, RedisSettings, create_pool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.models import Org, OrgStatus
from app.core.permissions.permset import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org

logger = logging.getLogger("schakl.jobs")

_arq_pool: ArqRedis | None = None


async def enqueue(function: str, *args: Any, **kwargs: Any) -> None:
    """Fire a one-off job at the ARQ worker by function name (#125).

    ``function`` must be contributed by a module via ``ModuleDescriptor.worker_functions``
    (or be a core worker function) — an unknown name sits in the queue and is dropped by arq.
    The pool is process-wide and lazy, like ``app.core.cache.get_redis``. This raises on a
    Redis failure; a caller for whom the job is a nicety (a first DNS fetch after create)
    catches and logs rather than failing the request it rides on.
    """
    global _arq_pool
    if _arq_pool is None:
        redis_settings = RedisSettings.from_dsn(settings.redis_url)
        # One attempt, not arq's default five-with-backoff: the caller either tolerates a
        # missing queue (and wants to know *now*) or fails its request — never hangs it.
        redis_settings.conn_retries = 1
        _arq_pool = await create_pool(redis_settings)
    await _arq_pool.enqueue_job(function, *args, **kwargs)

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
