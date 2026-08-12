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
from app.core.auth.backend import session_org
from app.core.auth.models import User
from app.core.auth.users import current_active_user_optional
from app.core.models import Membership, Org, OrgStatus
from app.core.permissions.catalog import ROLE_CLIENT
from app.core.permissions.models import MembershipRole, Role, RolePermission
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


def origin_from(host: str, proto: str = "") -> str:
    """``scheme://host[:port]`` from a forwarded host and (optional) forwarded scheme.

    The scheme has to be *guessed* when nothing forwards it, and the guess must not be "whatever
    scheme this connection used": every hop that reaches this service is plain HTTP — Traefik
    terminates TLS, and the SSR web app calls the API over the internal network — so reading it
    off the socket answers ``http`` for every production request and puts ``http://`` into a
    discovery document a client is about to trust. Loopback is the only host where plain HTTP is
    the real answer, so that is the only place it is assumed.
    """
    host = host.split(",", 1)[0].strip()
    proto = proto.split(",", 1)[0].strip()
    if not proto:
        proto = "http" if host.startswith(("localhost", "127.0.0.1", "[::1]")) else "https"
    return f"{proto}://{host}"


def external_origin(request: Request) -> str:
    """The origin the *browser* reached this deployment on — not the one we were called on.

    Distinct from :func:`request_hostname`, which drops the port because a port never resolves a
    tenant. An origin is a different question: it is echoed into OAuth discovery documents and
    redirect targets, where a dropped port sends a developer on ``localhost:5173`` to a server
    that is not running.
    """
    host = request.headers.get("x-forwarded-host") or request.headers.get("host", "")
    if not host:
        return str(request.base_url).rstrip("/")
    return origin_from(host, request.headers.get("x-forwarded-proto", ""))


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


#: Where :func:`request_org_id` parks its answer. Not an optimisation for the request path —
#: ``require_context`` resolves the org on its own session and never reads this — but for the
#: **pre-auth** flows, where the same host is resolved by a guard, by the account lookup and by
#: the route that mints the session, each on its own throwaway session (``manager.py``).
_ORG_ID_STATE = "schakl_request_org_id"
_UNRESOLVED = object()


async def request_org_id(request: Request) -> uuid.UUID | None:
    """The org this request's hostname resolves to, or ``None`` for a host that names no tenant.

    ``None`` is a real answer, not a failure: the cloud console runs on the apex, where no org
    resolves (docs/CLOUD.md). Callers decide what that means for them — the pre-auth ones treat
    it as "nothing to narrow", ``require_context`` never sees it (it 404s the unknown host).
    """
    cached = getattr(request.state, _ORG_ID_STATE, _UNRESOLVED)
    if cached is not _UNRESOLVED:
        return cached  # type: ignore[return-value]
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
    org_id = org.id if org is not None else None
    setattr(request.state, _ORG_ID_STATE, org_id)
    return org_id


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
    #: An **external** (client) login: a contact-linked portal membership (#193) *or* any
    #: membership holding the seeded ``client`` role (#274). Services use it where "what a
    #: client may see" is narrower than the horizon alone — e.g. only tasks ticked visible,
    #: only their companies' contacts. Both are the same kind of login: #252 already decided
    #: that "the client role marks a login as external", and gating these narrowings on the
    #: contact link alone left a directly-invited client reading the whole address book.
    is_portal: bool = False
    # Set only during an impersonation: ``user`` is then the impersonated member and
    # ``impersonated_by`` the real, authenticated principal behind them — an instance owner
    # (issue #26) or an agency staff member signed in as a client's contact (#296).
    # ``impersonation_kind`` says which (``app/core/impersonation.py``), because ending it and
    # recording it differ per kind. Every write made in this request carries the impersonator
    # onto the activity trail (§16), so a change is never attributed to the client alone.
    impersonated_by: User | None = None
    impersonation_expires_at: Any | None = None
    impersonation_kind: str | None = None
    #: True when a **portal** impersonation is running as *less* than the client actually holds,
    #: because the impersonator does not hold it either (#266). It is the one thing the banner
    #: must say out loud: the whole point of signing in as a client is to see what they see, so a
    #: silently narrowed view is a screen that lies — staff would report "the invoices aren't
    #: there" about a client who can see them perfectly well. ``False`` for an unnarrowed
    #: session, and always ``False`` for the instance kind, which never caps.
    impersonation_narrowed: bool = False
    #: True for a context built by ``app.core.jobs.system_context`` — a cron tick, or a
    #: provider callback nobody is signed in for. ``user`` is then a transient placeholder that
    #: exists in **no** ``users`` row, so anything that would *store* it must store nothing
    #: instead: ``activity_log.actor_user_id`` carries a FK, and the trail's own contract is
    #: that a NULL actor with no name is genuinely the system (§16). Without this flag a
    #: settle driven by a webhook raises a foreign-key violation inside the very transaction
    #: that books the payment — the money moves and nothing records it.
    is_system: bool = False

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

        # …and it must be a session **for this org** (``core/auth/backend.py``). The account
        # table is instance-level and the password check is tenant-blind, so "a valid session"
        # and "a session belonging here" were the same sentence only because a self-hosted box
        # has one org. Anything else — no claim at all (an instance session from the console's
        # apex, or a token minted before the claim existed) or a claim naming another org — is
        # not a session here, and says so with the same 401 as no session at all: it is an
        # authentication answer, and the browser's job is identical either way (log in). The
        # membership check below is a *different* question and stays where it is; passing it
        # would not make someone else's session ours.
        if session_org(request) != org.id:
            raise AppError("unauthorized", "errors.unauthorized", status_code=401)

        # Impersonation (issues #26, #296): a valid, time-boxed grant swaps the effective user;
        # authentication above stays the real principal. The grant names the org it was issued
        # for, so one can never be carried onto another tenant's hostname.
        from app.core.impersonation import KIND_PORTAL, read_impersonation

        impersonator: User | None = None
        expires_at = None
        impersonation_kind: str | None = None
        claims = read_impersonation(request, user)
        if claims is not None and claims.org_id == org.id:
            target = await session.get(User, claims.target_user_id)
            if target is not None and target.is_active:
                impersonator, user = user, target
                expires_at = claims.expires_at
                impersonation_kind = claims.kind

        # Verify membership *through* RLS. The permission fetch rides along on the same statement
        # — one round-trip, whatever the role count. It must stay *below* the impersonation swap
        # above: permissions resolve for the impersonated member, never for the instance owner,
        # and ``is_superuser`` never implies ``*``.
        #
        # ``array_agg(...).filter(...)`` is load-bearing: a bare ``array_agg`` over the LEFT JOIN
        # of a role-less membership yields ``{NULL}``, not an empty array.
        #
        # "Does this membership hold the seeded ``client`` role?" rides the same statement as a
        # ``bool_or`` (the ``roles`` join is 1:1 on ``membership_roles``, so it cannot fan the
        # permission aggregate out) — one round-trip, whatever the role count.
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
                )
                .group_by(Membership.id)
            )
        ).first()
        if row is None:
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        membership, granted, holds_client = row
        permissions = PermissionSet.of(granted)

        # A **portal** impersonation runs as the target *capped by the impersonator* (#266).
        # Staff signing in as their client see the client's screens, minus anything they could
        # not open on their own account — the invoices a member without an invoice read must not
        # reach through a client session. This replaces the `covers` refusal at the issue site:
        # it states the same invariant directly (the result is a subset of the caller's set by
        # construction) instead of forbidding the whole session, and it decouples the two, so
        # granting the `client` role a permission no longer silently narrows who may impersonate.
        #
        # Deliberately **not** the instance kind: an instance owner is trusted with everything by
        # definition (#26) and holds no membership in the tenant at all, so capping would resolve
        # to nothing and break cross-tenant support entirely.
        narrowed_by: PermissionSet | None = None
        ceiling_membership_id: uuid.UUID | None = None
        if impersonator is not None and impersonation_kind == KIND_PORTAL:
            ceiling_row = (
                await session.execute(
                    select(
                        Membership.id,
                        func.array_agg(RolePermission.permission).filter(
                            RolePermission.permission.is_not(None)
                        ),
                    )
                    .outerjoin(MembershipRole, MembershipRole.membership_id == Membership.id)
                    .outerjoin(
                        RolePermission, RolePermission.role_id == MembershipRole.role_id
                    )
                    .where(
                        Membership.user_id == impersonator.id,
                        Membership.org_id == org.id,
                    )
                    .group_by(Membership.id)
                )
            ).first()
            # No membership for the impersonator in this org means the grant outlived it. Cap to
            # nothing rather than trusting the target's set — fail closed, the session is theirs.
            ceiling = PermissionSet.of(ceiling_row[1] if ceiling_row else ())
            capped = permissions.narrowed_to(ceiling)
            if capped.granted != permissions.granted:
                narrowed_by = permissions
            permissions = capped
            ceiling_membership_id = ceiling_row[0] if ceiling_row else None

        # Company data horizon (issue #191): one indexed query over the assignment tables,
        # via the resolver seam (the tables belong to the companies module). A wildcard
        # holder (owner) is never restricted, whatever rows exist — never lock the tenant
        # out (§15) — so resolution is skipped entirely for them.
        #
        # The two facts the statement above already answered are handed *in*, never asked for
        # again (docs/PERFORMANCE.md): ``holds_client`` short-circuits the client-role floor's
        # own ``EXISTS``, and "did the portal source restrict?" is the same question as "is
        # this user contact-linked", so ``is_portal`` reads off the resolution instead of
        # re-running the contacts join. Together that was two extra round-trips on **every**
        # non-owner request in the app — including every ordinary staff one, which paid a
        # contacts query to be told it was not a client.
        from app.core.scope import SCOPE_SOURCE_PORTAL, resolve_company_scope_details

        if permissions.wildcard:
            company_scope: frozenset[uuid.UUID] | None = None
            is_portal = False
        else:
            resolution = await resolve_company_scope_details(
                session, org.id, membership.id, holds_client=bool(holds_client)
            )
            company_scope = resolution.scope
            # External login (#193 + #274): the client role marks one, and so does a contact
            # link — a client with no contact link is external too.
            is_portal = bool(holds_client) or SCOPE_SOURCE_PORTAL in resolution.sources

        # The horizon caps the same way, and for the same reason (#266). A contact may be linked
        # to more companies than the staff member impersonating them can see: the impersonate
        # endpoint loads the *contact* through the caller's repository, so it never reaches one
        # outside their horizon, but nothing bounded the companies behind it. Capping permissions
        # while leaving the horizon wide would trade one escalation for another.
        # ``None`` is "unrestricted" and acts as the identity, so an unrestricted impersonator
        # leaves the client's own horizon exactly as it was.
        if ceiling_membership_id is not None and company_scope != frozenset():
            ceiling_scope = (
                await resolve_company_scope_details(session, org.id, ceiling_membership_id)
            ).scope
            if ceiling_scope is not None:
                company_scope = (
                    ceiling_scope if company_scope is None else company_scope & ceiling_scope
                )

        ctx = RequestContext(
            user=user,
            org=org,
            session=session,
            membership_id=membership.id,
            permissions=permissions,
            company_scope=company_scope,
            is_portal=is_portal,
            impersonated_by=impersonator,
            impersonation_expires_at=expires_at,
            impersonation_kind=impersonation_kind,
            impersonation_narrowed=narrowed_by is not None,
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

    A ``company_id`` column is not the only way a row belongs to a client, and the ones that
    lack it are exactly where the horizon silently did nothing (#285): a website belongs to its
    *domain's* client, a contact to whatever ``company_contacts`` links it to. Such a model
    declares ``__company_horizon_clause__(scope)`` and returns the predicate itself; every path
    through this repository — ``get_or_404``, ``scoped_select``, ``scoped_count_select``,
    ``count`` — then carries it, which is the point of putting it here rather than in one
    module's ``list()``.
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
        # How this model anchors to a company, in precedence order: a clause it builds itself
        # (an indirect link — #285), else a column. `companies` names its own pk via
        # `__company_horizon_attr__`; every other model is matched on a `company_id` column.
        self._horizon_clause = getattr(model, "__company_horizon_clause__", None)
        attr = getattr(model, "__company_horizon_attr__", "company_id")
        self._horizon_col = getattr(model, attr, None)
        table_col = getattr(model, "__table__", None)
        table_col = table_col.c.get(attr) if table_col is not None else None
        self._horizon_nullable = bool(table_col.nullable) if table_col is not None else False

    def horizon_condition(self):
        """The company horizon as a standalone predicate, or ``None`` when unrestricted (#191).

        ``scoped_select()`` is the normal path and already carries this. A read that *cannot*
        be built from it — a window fold over a subquery, a hand-built ``count(DISTINCT …)`` —
        takes the predicate from here and ANDs it onto its own statement, so the horizon is
        still expressed in exactly one place. Interactions' folded feed re-derived nothing and
        simply had no horizon at all (#240); this is the seam it was missing.
        """
        if self.company_scope is None:
            return None
        if self._horizon_clause is not None:
            # The model owns the shape of its own company link (#285).
            return self._horizon_clause(self.company_scope)
        if self._horizon_col is None:
            return None
        col = self._horizon_col
        if self._horizon_nullable:
            # A row not attached to any company (a company-less task, shared-infra hosting)
            # is not company data; the horizon governs company rows only.
            return (col.is_(None)) | (col.in_(self.company_scope))
        return col.in_(self.company_scope)

    def _horizon(self, stmt):
        """AND the company horizon onto a statement (no-op when unrestricted, #191)."""
        condition = self.horizon_condition()
        return stmt if condition is None else stmt.where(condition)

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

    def scoped_count_select(self):
        """A ``select(func.count())`` over this model, tenant- **and horizon**-filtered.

        A list's ``total`` must count exactly the rows its query could return. Every service
        that hand-built ``select(func.count()).where(org_id == …)`` skipped the company
        horizon, so a scoped login read the org-wide count above its own filtered rows
        (#252) — build totals from this instead.
        """
        return self._horizon(
            select(func.count())
            .select_from(self.model)
            .where(self.model.org_id == self.org_id)
        )

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
