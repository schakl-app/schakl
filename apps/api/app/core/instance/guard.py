"""Gate for the instance-admin surface (issue #26).

Two independent switches: the deployment flag (``SCHAKL_INSTANCE_ADMIN_ENABLED``, off by
default — a single-tenant box has no business exposing a cross-tenant surface) and the
**instance owner** principal (``users.is_superuser``), deliberately distinct from an org's
``owner`` membership role. Disabled → 404, so the surface doesn't even advertise itself;
enabled but not an instance owner → 403.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.auth.users import current_active_user
from app.db import async_session_maker
from app.errors import AppError

if TYPE_CHECKING:
    from app.core.models import Org


@dataclass
class InstanceContext:
    """The authenticated instance owner and a session with **no tenant bound**.

    RLS therefore fails closed on every org-scoped table; code that needs tenant rows must
    bind the GUC to one org explicitly (and only that org) before touching them.
    """

    user: User
    session: AsyncSession


async def ensure_org_data_access(ctx: InstanceContext, org: Org) -> None:
    """Run before the instance surface reads *tenant data* of one org (detail, export,
    impersonation, module config). Lifecycle transitions (suspend/activate/delete) stay
    outside it on purpose: the platform must be able to enforce billing without consent.

    On a self-hosted box the superuser + flag gate above is the whole policy; the cloud
    posture (epic #199) additionally demands an org-issued, claimed service PIN. The import
    is lazy and flag-guarded so core never loads the business-licensed package elsewhere."""
    if settings.is_cloud:
        from app.core.cloud.access import ensure_cloud_org_access

        await ensure_cloud_org_access(ctx, org)


async def require_instance_admin(
    user: User = Depends(current_active_user),
) -> AsyncGenerator[InstanceContext, None]:
    if not settings.instance_admin_enabled:
        raise AppError("not_found", "errors.not_found", status_code=404)
    if not user.is_superuser:
        raise AppError("forbidden", "errors.forbidden", status_code=403)
    async with async_session_maker() as session:
        ctx = InstanceContext(user=user, session=session)
        try:
            yield ctx
            await session.commit()
        except Exception:
            await session.rollback()
            raise
