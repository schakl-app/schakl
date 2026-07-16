"""Tenancy layer (CLAUDE.md §5, Golden Rule 1).

One dependency, ``require_context``, yields ``(current_user, current_org)`` plus a
tenant-bound session, and is the *only* sanctioned way domain routers touch data. It:

1. authenticates the (global) user;
2. resolves ``current_org`` from the request hostname (``orgs`` has no RLS);
3. binds the RLS GUC to that org, then verifies the user's membership *through* RLS;
4. resolves that membership's **effective permissions** in the same round-trip (issue #19);
5. hands work a ``TenantScopedRepository`` that auto-injects ``org_id`` on every operation.

App-layer filtering and Postgres RLS thus enforce the same boundary from both sides. RLS answers
"which tenant?"; the permission set answers "may they?" — the two never mix.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth.models import User
from app.core.auth.users import current_active_user_optional
from app.core.models import Membership, Org, OrgStatus
from app.core.permissions.models import MembershipRole, RolePermission
from app.core.permissions.permset import PermissionSet
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
    """Resolve the tenant strictly from the request hostname.

    Exactly two ways a host maps to an org — a **verified** custom domain, or
    ``<slug>.<base_domain>``. Anything else resolves to nothing and the caller must fail
    explicitly (issue #26): guessing "the only org" would serve tenant data on any typo'd
    or hijacked hostname. Soft-deleted orgs no longer resolve at all; suspended orgs do
    resolve (the login screen still needs their branding) and ``require_context`` blocks them.
    """
    if not host:
        return None

    org = await session.scalar(
        select(Org).where(
            Org.custom_domain == host,
            Org.custom_domain_verified_at.is_not(None),
        )
    )
    if org is None:
        base = settings.base_domain.lower()
        if host.endswith("." + base):
            slug = host[: -(len(base) + 1)]
            org = await session.scalar(select(Org).where(Org.slug == slug))
    if org is None or org.status == OrgStatus.DELETED.value:
        return None
    return org


# --------------------------------------------------------------------------- #
# Request context
# --------------------------------------------------------------------------- #
@dataclass
class RequestContext:
    """Everything a tenant-scoped handler needs. Yielded by ``require_context``."""

    user: User
    org: Org
    session: AsyncSession
    membership_id: uuid.UUID | None = None
    #: Effective permissions of this membership — the union over every role it holds, resolved
    #: once in ``require_context``. Never re-query per check (docs/PERFORMANCE.md).
    permissions: PermissionSet = field(default_factory=PermissionSet)
    #: The company data horizon (issue #191), resolved once alongside the membership:
    #: ``None`` = unrestricted (the default and the owner's guarantee); a set = this
    #: membership sees only those companies' rows. Enforced by the repository below.
    company_scope: frozenset[uuid.UUID] | None = None
    # Set only during an instance-admin impersonation (issue #26): ``user`` is then the
    # impersonated member and ``impersonated_by`` the real, authenticated instance owner.
    impersonated_by: User | None = None
    impersonation_expires_at: Any | None = None

    def repo(self, model: type[ModelT]) -> TenantScopedRepository[ModelT]:
        return TenantScopedRepository(
            self.session, self.org.id, model, company_scope=self.company_scope
        )

    # --- authorization (issue #19) ----------------------------------------- #
    def can(self, permission: str, scope: str | None = None) -> bool:
        """Does the caller hold ``permission``? ``scope=None`` means "at some scope"."""
        return self.permissions.has(permission, scope)

    def require(self, permission: str, scope: str | None = None) -> None:
        if not self.can(permission, scope):
            raise AppError("forbidden", "errors.forbidden", status_code=403)

    # --- pool hygiene (docs/PERFORMANCE.md) --------------------------------- #
    @asynccontextmanager
    async def release_db(self) -> AsyncGenerator[None, None]:
        """Hand the pooled DB connection back while awaiting an external service.

        A request runs as **one transaction** with the RLS GUC bound (``app/db.py``), so its
        session pins one pool connection from its first query until the response commits.
        Held across a slow external call — Google APIs take seconds, up to their 20 s
        timeout — a handful of such requests drains the pool and every other request queues
        on checkout until ``pool_timeout``, which reads as a sitewide freeze. **Wrap every
        in-request external HTTP call in this**; background jobs run in their own process
        and pool and don't need it.

        Entry commits the transaction (returning the connection to the pool); exit re-binds
        the RLS GUC on a fresh one. Two rules inside the block:

        - **Never touch the session.** A query would check a connection back out *without*
          the GUC bound and fail closed (RLS: no rows). Mutating already-loaded ORM objects
          is fine — that is memory, not I/O — and flushes after the block.
        - **Only pending work you are happy to persist.** The entry commit is a real commit;
          writes that must roll back together with a later failure belong after the block.
        """
        await self.session.commit()
        try:
            yield
        finally:
            # First statement of the new transaction: bind the GUC before any query runs.
            await set_current_org(self.session, self.org.id)


async def require_context(
    request: Request,
    user: User | None = Depends(current_active_user_optional),
) -> AsyncGenerator[RequestContext, None]:
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("unknown_host", "errors.unknown_host", status_code=404)
        if org.status == OrgStatus.SUSPENDED.value:
            raise AppError("org_suspended", "errors.org_suspended", status_code=403)

        # Bind RLS to this org up front: it must be set before the API-key lookup (which is
        # tenant-scoped, so a key from another org is simply not found) and before the membership
        # read below.
        await set_current_org(session, org.id)

        # API-key authentication (#20): if the request carries a key, it yields the same
        # RequestContext a session would — resolved to the owner (personal) or a synthetic
        # principal (service account), with permissions capped to the key's scopes.
        from app.core.apikeys.auth import resolve_api_key_context

        api_ctx = await resolve_api_key_context(request, session, org)
        if api_ctx is not None:
            try:
                yield api_ctx
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return

        # No key → a session is required.
        if user is None:
            raise AppError("unauthorized", "errors.unauthorized", status_code=401)

        # Instance-admin impersonation (issue #26): a valid, time-boxed grant swaps the
        # effective user; authentication above stays the real (superuser) principal.
        from app.core.instance.impersonation import read_impersonation

        impersonator: User | None = None
        expires_at = None
        claims = read_impersonation(request, user)
        if claims is not None and claims.org_id == org.id:
            target = await session.get(User, claims.target_user_id)
            if target is not None and target.is_active:
                impersonator, user = user, target
                expires_at = claims.expires_at

        # Verify membership *through* RLS. The permission fetch rides along on the same statement
        # — one round-trip, whatever the role count. It must stay *below* the impersonation swap
        # above: permissions resolve for the impersonated member, never for the instance owner,
        # and ``is_superuser`` never implies ``*``.
        #
        # ``array_agg(...).filter(...)`` is load-bearing: a bare ``array_agg`` over the LEFT JOIN
        # of a role-less membership yields ``{NULL}``, not an empty array.
        row = (
            await session.execute(
                select(
                    Membership,
                    func.array_agg(RolePermission.permission).filter(
                        RolePermission.permission.is_not(None)
                    ),
                )
                .outerjoin(MembershipRole, MembershipRole.membership_id == Membership.id)
                .outerjoin(RolePermission, RolePermission.role_id == MembershipRole.role_id)
                .where(
                    Membership.user_id == user.id,
                    Membership.org_id == org.id,
                )
                .group_by(Membership.id)
            )
        ).first()
        if row is None:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        membership, granted = row
        permissions = PermissionSet.of(granted)

        # Company data horizon (issue #191): one indexed query over the assignment tables,
        # via the resolver seam (the tables belong to the companies module). A wildcard
        # holder (owner) is never restricted, whatever rows exist — never lock the tenant
        # out (§15) — so resolution is skipped entirely for them.
        from app.core.scope import resolve_company_scope

        company_scope = (
            None
            if permissions.wildcard
            else await resolve_company_scope(session, org.id, membership.id)
        )

        ctx = RequestContext(
            user=user,
            org=org,
            session=session,
            membership_id=membership.id,
            permissions=permissions,
            company_scope=company_scope,
            impersonated_by=impersonator,
            impersonation_expires_at=expires_at,
        )
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

    It also enforces the **company data horizon** (issue #191): with a restricted
    ``company_scope``, every model carrying ``company_id`` filters to those companies (rows
    with no company linkage stay visible — they are not company data), companies themselves
    filter by ``id``, and writes cannot place a row onto an invisible company. Out-of-horizon
    reads answer 404, never 403 — a 403 on get-by-id leaks existence (#19's ``_owned_or_404``
    reasoning).
    """

    def __init__(
        self,
        session: AsyncSession,
        org_id: uuid.UUID,
        model: type[ModelT],
        *,
        company_scope: frozenset[uuid.UUID] | None = None,
    ) -> None:
        self.session = session
        self.org_id = org_id
        self.model = model
        self.company_scope = company_scope
        # Which column anchors the model to a company: `companies` itself declares its pk via
        # `__company_horizon_attr__`; every other model is matched on a `company_id` column.
        attr = getattr(model, "__company_horizon_attr__", "company_id")
        self._horizon_col = getattr(model, attr, None)
        table_col = getattr(model, "__table__", None)
        table_col = table_col.c.get(attr) if table_col is not None else None
        self._horizon_nullable = bool(table_col.nullable) if table_col is not None else False

    def _horizon(self, stmt):
        """AND the company horizon onto a statement (no-op when unrestricted, #191)."""
        if self.company_scope is None or self._horizon_col is None:
            return stmt
        col = self._horizon_col
        if self._horizon_nullable:
            # A row not attached to any company (a company-less task, shared-infra hosting)
            # is not company data; the horizon governs company rows only.
            return stmt.where((col.is_(None)) | (col.in_(self.company_scope)))
        return stmt.where(col.in_(self.company_scope))

    def _guard_company_write(self, values: dict[str, Any]) -> None:
        """Refuse placing a row onto a company outside the horizon (#191) — as a 404, the
        same answer reading that company gets, so writes don't leak existence either."""
        if self.company_scope is None:
            return
        company_id = values.get("company_id")
        if company_id is not None and company_id not in self.company_scope:
            raise AppError("not_found", "errors.not_found", status_code=404)

    def _scoped(self):
        return self._horizon(select(self.model).where(self.model.org_id == self.org_id))

    def scoped_select(self):
        """A ``select(model)`` already filtered to this tenant.

        Use for reads that need conditions beyond simple equality (date ranges, ``IS NULL``):
        the caller adds ``.where(...)`` but the ``org_id`` filter is always present, so a query
        built this way can never leak across tenants (Golden Rule 1). The company horizon
        (#191) rides along the same way.
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
        stmt = self._horizon(self._apply_filters(stmt, filters))
        return int(await self.session.scalar(stmt) or 0)

    async def create(self, **values: Any) -> ModelT:
        # You cannot create a row onto a company you cannot see (#191).
        self._guard_company_write(values)
        obj = self.model(org_id=self.org_id, **values)
        self.session.add(obj)
        await self.session.flush()
        # Load server-side defaults (timestamps) so serialization never lazy-loads.
        await self.session.refresh(obj)
        return obj

    async def update(self, obj: ModelT, **values: Any) -> ModelT:
        if getattr(obj, "org_id") != self.org_id:  # noqa: B009 - defensive cross-tenant guard
            raise AppError("not_found", "errors.not_found", status_code=404)
        # …nor move one onto a company you cannot see (#191).
        self._guard_company_write(values)
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
