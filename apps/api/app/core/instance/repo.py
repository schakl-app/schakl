"""The one sanctioned place a query crosses tenants (issue #26, Golden Rule 1).

Slug and custom-domain uniqueness are **global** invariants — they route hostnames to orgs —
so they cannot be answered through the tenant-scoped repository. Rather than loosening that
layer, every legitimately-unscoped read lives here: narrow, read-only, and auditable in one
file. ``orgs`` carries no RLS (it *is* the tenant table), so these queries need no GUC.

Nothing in this module writes, and nothing outside this package may import it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Org


async def org_count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(Org)) or 0)


async def list_orgs(session: AsyncSession) -> Sequence[Org]:
    return (await session.execute(select(Org).order_by(Org.created_at.asc()))).scalars().all()


async def get_org(session: AsyncSession, org_id: uuid.UUID) -> Org | None:
    return await session.get(Org, org_id)


async def slug_taken(
    session: AsyncSession, slug: str, *, exclude_org_id: uuid.UUID | None = None
) -> bool:
    stmt = select(Org.id).where(Org.slug == slug)
    if exclude_org_id is not None:
        stmt = stmt.where(Org.id != exclude_org_id)
    return (await session.scalar(stmt)) is not None


async def domain_taken(
    session: AsyncSession, domain: str, *, exclude_org_id: uuid.UUID | None = None
) -> bool:
    """Is ``domain`` claimed by any org — active custom domain *or* a pending claim?

    A pending claim counts: two orgs racing to verify the same domain would otherwise both
    pass the uniqueness check and collide at verification time.
    """
    stmt = select(Org.id).where(
        or_(Org.custom_domain == domain, Org.pending_domain == domain)
    )
    if exclude_org_id is not None:
        stmt = stmt.where(Org.id != exclude_org_id)
    return (await session.scalar(stmt)) is not None
