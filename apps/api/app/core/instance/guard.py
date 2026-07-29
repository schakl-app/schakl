"""Gate for the instance-admin surface (issue #26).

Two independent switches, then a capability check:

* the deployment flag (``SCHAKL_INSTANCE_ADMIN_ENABLED``, off by default — a single-tenant box
  has no business exposing a cross-tenant surface). Disabled → **404**, so the surface does not
  even advertise itself;
* the principal. Two of them, and only one can delegate:

  - **owner** — ``users.is_superuser``, deliberately distinct from an org's ``owner`` role.
    Holds every capability implicitly, exactly as the org ``owner`` role holds ``["*"]``
    (CLAUDE.md §15), and is the only principal that may grant instance access to anyone.
  - **admin** — a row in ``instance_admins`` holding an explicit capability set. Reaches the
    surface, and then only the routes whose declared capability it holds.

  Neither → **403**.

Then, per route, :func:`require_capability` — the instance analogue of ``require_permission``.
The route declares the key; the guard resolves the holder's set once per request. A route that
declares neither a capability nor an explicit exemption is a build break, enforced by
``tests/test_instance_deny_by_default.py`` exactly as §15's sweep does for tenant routes.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.auth.users import current_active_user
from app.core.instance.capabilities import CAPABILITY_KEYS
from app.db import async_session_maker
from app.errors import AppError

if TYPE_CHECKING:
    from app.core.models import Org

#: Marker read by the deny-by-default sweep, mirroring ``PERMISSION_MARKER`` in
#: ``app.core.permissions.deps``.
CAPABILITY_MARKER = "__schakl_instance_capability__"
CAPABILITY_EXEMPTION_MARKER = "__schakl_instance_capability_exempt__"


@dataclass
class InstanceContext:
    """The authenticated instance principal and a session with **no tenant bound**.

    RLS therefore fails closed on every org-scoped table; code that needs tenant rows must
    bind the GUC to one org explicitly (and only that org) before touching them.
    """

    user: User
    session: AsyncSession
    #: True for ``users.is_superuser``. Implies every capability, and is the *only* principal
    #: allowed to manage instance access — see ``require_instance_owner_principal``.
    is_owner: bool = False
    #: What a delegated admin was granted. Empty for an owner, whose ``can`` short-circuits.
    capabilities: frozenset[str] = field(default_factory=frozenset)

    def can(self, capability: str) -> bool:
        return self.is_owner or capability in self.capabilities

    def require(self, capability: str) -> None:
        if not self.can(capability):
            raise AppError("forbidden", "errors.forbidden", status_code=403)

    @property
    def effective_capabilities(self) -> list[str]:
        """What this principal actually holds, owner expanded. For display and /instance/me."""
        return sorted(CAPABILITY_KEYS) if self.is_owner else sorted(self.capabilities)


async def ensure_org_data_access(ctx: InstanceContext, org: Org) -> None:
    """Run before the instance surface reads *tenant data* of one org (detail, export,
    impersonation, module config). Lifecycle transitions (suspend/activate/delete) stay
    outside it on purpose: the platform must be able to enforce billing without consent.

    On a self-hosted box the principal gate above is the whole policy; the cloud posture
    (epic #199) additionally demands an org-issued, claimed service PIN — so a capability is
    necessary but never sufficient there. The import is lazy and flag-guarded so core never
    loads the business-licensed package elsewhere."""
    if settings.is_cloud:
        from app.core.cloud.access import ensure_cloud_org_access

        await ensure_cloud_org_access(ctx, org)


async def load_principal(session: AsyncSession, user: User) -> tuple[bool, frozenset[str]]:
    """``(is_owner, capabilities)``. One statement, and none at all for an owner."""
    if user.is_superuser:
        return True, frozenset()
    from app.core.models import InstanceAdmin

    granted = await session.scalar(
        select(InstanceAdmin.capabilities).where(InstanceAdmin.user_id == user.id)
    )
    if granted is None:
        return False, frozenset()
    # Intersect with the catalog: a capability removed from the code in a later release must
    # stop being honoured even while the stale key is still sitting in the row.
    return False, frozenset(granted) & CAPABILITY_KEYS


async def require_instance_admin(
    user: User = Depends(current_active_user),
) -> AsyncGenerator[InstanceContext, None]:
    if not settings.instance_admin_enabled:
        raise AppError("not_found", "errors.not_found", status_code=404)
    async with async_session_maker() as session:
        is_owner, capabilities = await load_principal(session, user)
        if not is_owner and not capabilities:
            # No grant at all, or a grant that has been emptied: not on this surface.
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        ctx = InstanceContext(
            user=user, session=session, is_owner=is_owner, capabilities=capabilities
        )
        try:
            yield ctx
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def require_capability(capability: str):
    """Declare the capability a route needs. Owners short-circuit.

    Returns a dependency, so it composes with ``require_instance_admin`` the same way
    ``require_permission`` composes with ``require_context`` (CLAUDE.md §15's "two layers":
    the route declares, the service refines).
    """
    if capability not in CAPABILITY_KEYS:  # pragma: no cover — a typo is a startup failure
        raise ValueError(f"unknown instance capability: {capability!r}")

    async def dependency(ctx: InstanceContext = Depends(require_instance_admin)) -> None:
        ctx.require(capability)

    setattr(dependency, CAPABILITY_MARKER, capability)
    return Depends(dependency)


def no_capability_required(reason: str):
    """For the few routes that legitimately answer without one. The reason is required and
    has to be about *what the route returns*, not about convenience."""

    async def dependency() -> None:
        return None

    setattr(dependency, CAPABILITY_EXEMPTION_MARKER, reason)
    return Depends(dependency)


async def require_instance_owner_principal(
    ctx: InstanceContext = Depends(require_instance_admin),
) -> InstanceContext:
    """Owner-only, for managing who has instance access.

    Delegation is **not** a capability on purpose: an admin who could grant
    ``instance.impersonate`` to themselves is an owner with extra steps, so the escalation edge
    is removed rather than guarded. A delegated admin gets 403 here — not 404 — because they
    are legitimately on this surface and simply may not do this.
    """
    if not ctx.is_owner:
        raise AppError("forbidden", "errors.forbidden", status_code=403)
    return ctx
