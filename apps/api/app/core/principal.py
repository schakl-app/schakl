"""A request context for a **named member**, built outside a request.

Background work acts as the system (``app.core.jobs.system_context``: a placeholder user, every
permission) or on explicit user ids. Neither is right for a write a real person *asked for* in
absentia — an employee mailing ``taak@bureau.nl`` expects the task to be theirs, to be refused
where they would have been refused, and to land inside their own company horizon. That is the
question the personal API-key path already answers for its owner (``apikeys/auth.py``), and it
is answered here with the same statement so the two cannot drift: the membership, the union of
its roles' permissions, the client-role flag and the company horizon, in one round trip.

``None`` means *no such member here*: no membership, a deactivated one, or a disabled account —
the same three answers ``require_context`` refuses a live session on, because a worker acting on
a departed colleague's behalf is exactly the session that was closed on their desk.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.models import User
from app.core.models import Membership, Org
from app.core.permissions.catalog import ROLE_CLIENT
from app.core.permissions.models import MembershipRole, Role, RolePermission
from app.core.permissions.permset import PermissionSet
from app.core.scope import SCOPE_SOURCE_PORTAL, resolve_company_scope_details
from app.core.tenancy import RequestContext


async def member_context(
    session: AsyncSession, org: Org, user_id: uuid.UUID
) -> RequestContext | None:
    """The context a request from this member would have run with, or ``None``."""
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        return None
    row = (
        await session.execute(
            select(
                Membership,
                func.array_agg(RolePermission.permission).filter(
                    RolePermission.permission.is_not(None)
                ),
                func.bool_or(Role.key == ROLE_CLIENT),
            )
            .outerjoin(MembershipRole, MembershipRole.membership_id == Membership.id)
            .outerjoin(Role, Role.id == MembershipRole.role_id)
            .outerjoin(RolePermission, RolePermission.role_id == MembershipRole.role_id)
            .where(
                Membership.user_id == user.id,
                Membership.org_id == org.id,
                Membership.deactivated_at.is_(None),
            )
            .group_by(Membership.id)
        )
    ).first()
    if row is None:
        return None
    membership, granted, holds_client = row
    permissions = PermissionSet.of(granted)
    if permissions.wildcard:
        company_scope: frozenset[uuid.UUID] | None = None
        is_portal = False
    else:
        resolution = await resolve_company_scope_details(
            session, org.id, membership.id, holds_client=bool(holds_client)
        )
        company_scope = resolution.scope
        is_portal = bool(holds_client) or SCOPE_SOURCE_PORTAL in resolution.sources
    return RequestContext(
        user=user,
        org=org,
        session=session,
        membership_id=membership.id,
        permissions=permissions,
        company_scope=company_scope,
        is_portal=is_portal,
    )


__all__ = ["member_context"]
