"""Tenancy layer (CLAUDE.md §5, Golden Rule 1).

One dependency, ``require_context``, yields ``(current_user, current_org, role)`` plus a
tenant-bound session, and is the *only* sanctioned way domain routers touch data. It:

1. authenticates the (global) user;
2. resolves ``current_org`` from the request hostname (``orgs`` has no RLS);
3. binds the RLS GUC to that org, then verifies the user's membership *through* RLS;
4. hands work a ``TenantScopedRepository`` that auto-injects ``org_id`` on every operation.

App-layer filtering and Postgres RLS thus enforce the same boundary from both sides.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Sequence
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.auth.users import current_active_user
from app.core.models import Membership, Org
from app.core.roles import Role
from app.db import async_session_maker, set_current_org
from app.errors import AppError

ModelT = TypeVar("ModelT")


# --------------------------------------------------------------------------- #
# Org resolution
# --------------------------------------------------------------------------- #
def request_hostname(request: Request) -> str:
    """The tenant hostname for this request.

    Prefers ``X-Forwarded-Host`` (set by Traefik, and by the SSR web app when it calls the API
    on a user's behalf) so tenant resolution reflects the *browser's* host, not the internal
    service address. Falls back to ``Host``.
    """
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    return host.split(",", 1)[0].split(":", 1)[0].strip().lower()


async def resolve_org(session: AsyncSession, host: str) -> Org | None:
    """Resolve the tenant from a request hostname (custom domain or ``<slug>.base_domain``).

    Falls back to the single seeded org for self-hosted, single-tenant installs where the
    hostname (e.g. ``api.localhost``) doesn't encode a slug — never a shortcut that assumes
    one org, just a sensible default when resolution is ambiguous.
    """
    if host:
        # custom_domain lives on org_settings; match there first.
        from app.core.models import OrgSettings

        settings_hit = await session.scalar(
            select(OrgSettings).where(OrgSettings.custom_domain == host)
        )
        if settings_hit is not None:
            return await session.get(Org, settings_hit.org_id)

        base = settings.base_domain.lower()
        if host.endswith("." + base):
            slug = host[: -(len(base) + 1)]
            org = await session.scalar(select(Org).where(Org.slug == slug))
            if org is not None:
                return org

    # Fallback: the configured seed slug, else the only org if exactly one exists.
    org = await session.scalar(select(Org).where(Org.slug == settings.seed_org_slug))
    if org is not None:
        return org
    orgs = (await session.execute(select(Org).limit(2))).scalars().all()
    return orgs[0] if len(orgs) == 1 else None


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #
@dataclass
class RequestContext:
    """Everything a tenant-scoped handler needs. Yielded by ``require_context``."""

    user: User
    org: Org
    role: Role
    session: AsyncSession

    def repo(self, model: type[ModelT]) -> TenantScopedRepository[ModelT]:
        return TenantScopedRepository(self.session, self.org.id, model)

    @property
    def can_write(self) -> bool:
        # Clients are read-only; staff (owner/admin/member) may mutate.
        return self.role != Role.CLIENT

    def ensure_can_write(self) -> None:
        if not self.can_write:
            raise AppError("forbidden", "errors.forbidden", status_code=403)

    def ensure_can_manage(self) -> None:
        if not self.role.can_manage:
            raise AppError("forbidden", "errors.forbidden", status_code=403)


async def require_context(
    request: Request,
    user: User = Depends(current_active_user),
) -> AsyncGenerator[RequestContext, None]:
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("not_found", "errors.not_found", status_code=404)

        # Bind RLS to this org, then verify membership *through* RLS.
        await set_current_org(session, org.id)
        membership = await session.scalar(
            select(Membership).where(
                Membership.user_id == user.id,
                Membership.org_id == org.id,
            )
        )
        if membership is None:
            raise AppError("forbidden", "errors.forbidden", status_code=403)

        ctx = RequestContext(user=user, org=org, role=Role(membership.role), session=session)
        try:
            yield ctx
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# --------------------------------------------------------------------------- #
# Tenant-scoped repository — the only sanctioned data path for domain models
# --------------------------------------------------------------------------- #
class TenantScopedRepository(Generic[ModelT]):
    """Auto-injects ``org_id`` on writes and filters it on reads.

    RLS is defence-in-depth; this is the primary guard. Never bypass it with a raw,
    unscoped query (Golden Rule 1 / CLAUDE.md §5).
    """

    def __init__(self, session: AsyncSession, org_id: uuid.UUID, model: type[ModelT]) -> None:
        self.session = session
        self.org_id = org_id
        self.model = model

    def _scoped(self):
        return select(self.model).where(self.model.org_id == self.org_id)

    def scoped_select(self):
        """A ``select(model)`` already filtered to this tenant.

        Use for reads that need conditions beyond simple equality (date ranges, ``IS NULL``):
        the caller adds ``.where(...)`` but the ``org_id`` filter is always present, so a query
        built this way can never leak across tenants (Golden Rule 1).
        """
        return self._scoped()

    def _apply_filters(self, stmt, filters: dict[str, Any]):
        for key, value in filters.items():
            stmt = stmt.where(getattr(self.model, key) == value)
        return stmt

    async def get(self, entity_id: uuid.UUID) -> ModelT | None:
        return await self.session.scalar(self._scoped().where(self.model.id == entity_id))

    async def get_or_404(self, entity_id: uuid.UUID) -> ModelT:
        obj = await self.get(entity_id)
        if obj is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return obj

    async def list(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        order_by: Any | None = None,
        **filters: Any,
    ) -> Sequence[ModelT]:
        stmt = self._apply_filters(self._scoped(), filters).limit(limit).offset(offset)
        stmt = stmt.order_by(order_by if order_by is not None else self.model.created_at.desc())
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self, **filters: Any) -> int:
        stmt = select(func.count()).select_from(self.model).where(
            self.model.org_id == self.org_id
        )
        stmt = self._apply_filters(stmt, filters)
        return int(await self.session.scalar(stmt) or 0)

    async def create(self, **values: Any) -> ModelT:
        obj = self.model(org_id=self.org_id, **values)
        self.session.add(obj)
        await self.session.flush()
        # Load server-side defaults (timestamps) so serialization never lazy-loads.
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, **values: Any) -> ModelT:
        if getattr(obj, "org_id") != self.org_id:  # noqa: B009 - defensive cross-tenant guard
            raise AppError("not_found", "errors.not_found", status_code=404)
        for key, value in values.items():
            setattr(obj, key, value)
        await self.session.flush()
        # Refresh so the server-side ``updated_at`` (onupdate) is populated for serialization.
        await self.session.refresh(obj)
        return obj

    async def delete(self, obj: ModelT) -> None:
        if getattr(obj, "org_id") != self.org_id:  # noqa: B009
            raise AppError("not_found", "errors.not_found", status_code=404)
        await self.session.delete(obj)
        await self.session.flush()
