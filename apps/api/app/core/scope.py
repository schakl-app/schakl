"""Company data horizon (issue #191) — the resolver seam.

The third authorization axis: RLS isolates *tenants* (Golden Rule 1), RBAC scopes
*capability within* a tenant (#19), and the **company horizon** scopes *which company rows*
a membership may see — a per-membership set of company ids, or ``None`` for unrestricted.

The horizon's tables (company groups and their assignments) belong to the **companies
module**, and core never imports a module's internals (CLAUDE.md §6). So the module
registers its resolver here at import time and ``require_context`` calls through the seam —
exactly the event-bus shape. No resolver registered (companies module disabled — it is the
hub, so effectively never) means every membership is unrestricted.

Semantics (design-binding, #191):

* No assignment rows → ``None`` → sees **all** companies (fully backwards compatible).
* Assignments → sees only the **union** of the groups' companies (possibly the empty set).
* A wildcard (owner) membership is **never** restricted — the caller skips resolution; §15's
  "never lock the tenant out" reasoning: a misconfiguration must stay fixable by someone.
* RLS stays tenant-only — the horizon is app-layer, like permissions, never a policy.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # ``tenancy`` imports this module, so the type is a forward reference only.
    from app.core.tenancy import RequestContext

#: A model whose **external (client) login** rule is stricter than its staff horizon declares it
#: under this name; both reference seams prefer it for an ``is_portal`` caller. Defined here
#: rather than in ``directory.py`` because ``entity_visible`` below needs the same fact and
#: importing that module would cycle — it imports this one.
PORTAL_CLAUSE_ATTR = "__portal_horizon_clause__"

CompanyScopeResolver = Callable[
    [AsyncSession, uuid.UUID, uuid.UUID], Awaitable[frozenset[uuid.UUID] | None]
]

#: Resolver keys. A source is named so a caller who **already knows** its answer can skip it,
#: and so the *reason* a membership ended up restricted survives resolution. Both matter on
#: the hot path: ``require_context`` resolves the client-role fact on the membership statement
#: (a ``bool_or`` beside the permission aggregate) and needs to know whether the portal source
#: restricted — and re-deriving those two answers cost a second and a third round-trip on
#: every non-owner request in the app (docs/PERFORMANCE.md).
SCOPE_SOURCE_CLIENT_ROLE = "client_role"
SCOPE_SOURCE_PORTAL = "portal"
SCOPE_SOURCE_COMPANY_GROUPS = "company_groups"

_resolvers: dict[str, CompanyScopeResolver] = {}
_EMPTY: frozenset[uuid.UUID] = frozenset()

#: ``entity_type`` -> the model that owns it, for the core surfaces addressed by *entity
#: reference* rather than by their own id (#285): the activity trail
#: (``?entity_type=company&entity_id=…``) and a record's file list. Those two take the pair from
#: the caller and answered from the whole tenant, so a membership scoped to one company group
#: read the change history and the attached documents of clients it cannot see — the record was
#: 404, its paper trail was not.
#:
#: Filled by ``AuditableMixin`` / ``CustomizableMixin``, which already key on ``__entity_type__``.
#: Core still holds no module list: the model registers itself, and the *horizon* rule stays the
#: model's own (a ``company_id`` column, or its ``__company_horizon_clause__``).
_horizon_entities: dict[str, type] = {}


def register_horizon_entity(entity_type: str, model: type) -> None:
    _horizon_entities[entity_type] = model


def horizon_entity_model(entity_type: str) -> type | None:
    """The model behind an ``entity_type``, or ``None`` for a type with no record of its own
    (``avatar``, ``hr_document``, an unknown string)."""
    return _horizon_entities.get(entity_type)


async def entity_visible(
    ctx: RequestContext, entity_type: str, entity_id: uuid.UUID
) -> bool:
    """May this caller see the record an ``(entity_type, entity_id)`` pair names?

    For an unrestricted membership — every owner, and everyone with no group assignment — this
    is ``True`` without a query. Only a restricted one pays for the lookup, and it is the
    record's *own* repository that answers, so the horizon rule lives in one place and an
    indirect link (a website's domain, a contact's ``company_contacts``) is honoured too.

    An **external (client) login** gets the model's stricter ``__portal_horizon_clause__``
    where it declares one — the rule ``app/core/directory.py`` already applies at the other
    reference seam, and the reason this one had to learn it too (#266). The two seams
    disagreeing was reachable through the **file list**: ``GET /files`` takes the pair from
    the caller and declares ``no_permission_required`` ("any signed-in member", which
    includes a portal login), so this is its only gate — and the staff answer, a plain
    ``company_id`` match, admits the agency's *draft* invoice. A client held off that draft
    everywhere else could still enumerate the documents attached to it. The activity trail
    was never exposed the same way (its router returns ``[]`` for any portal caller before
    reaching here), which is exactly why the rule belongs in one place rather than in each
    caller: one of the two remembered and one did not.

    §15's failure mode (4), one layer in: holding the type's read permission is not the same
    as being able to see *that row* — and neither is passing the horizon a **staff** member
    would pass.

    ``True`` for a type with no model behind it: ``avatar`` and ``hr_document`` are keyed by
    user id and have their own rules at the call site. Guessing here would silently gate them.
    """
    if ctx.company_scope is None:
        return True
    model = _horizon_entities.get(entity_type)
    if model is None:
        return True
    portal_clause = getattr(model, PORTAL_CLAUSE_ATTR, None) if ctx.is_portal else None
    if portal_clause is not None:
        return (
            await ctx.session.scalar(
                select(model.id).where(
                    model.org_id == ctx.org.id,
                    model.id == entity_id,
                    portal_clause(ctx.company_scope),
                )
            )
        ) is not None
    return await ctx.repo(model).get(entity_id) is not None


def register_company_scope_resolver(resolver: CompanyScopeResolver, *, key: str) -> None:
    """Called once per owning module's package ``__init__``. More than one source can bound
    a membership (company groups #191, a portal contact's companies #193); each resolver
    answers ``None`` for "this source doesn't restrict them". ``key`` names the source — see
    the ``SCOPE_SOURCE_*`` constants."""
    _resolvers[key] = resolver


@dataclass(frozen=True)
class CompanyScopeResolution:
    """A horizon **and which sources produced it**.

    ``sources`` is not bookkeeping: "did the portal source restrict this membership?" *is* the
    question "is this user linked to a contact?", which the caller would otherwise ask the
    contacts module a second time.
    """

    scope: frozenset[uuid.UUID] | None
    sources: frozenset[str]


async def resolve_company_scope_details(
    session: AsyncSession,
    org_id: uuid.UUID,
    membership_id: uuid.UUID,
    *,
    holds_client: bool | None = None,
) -> CompanyScopeResolution:
    """Resolve the horizon, reporting which sources restricted.

    Restricting sources **union** (#193): a portal contact linked to two companies who is
    also assigned a group sees the union — while a membership no source restricts stays
    unrestricted. The union of restrictions can never widen past "everything", so combining
    with ``None`` (unrestricted) collapses to the restricted sets only.

    ``holds_client`` lets a caller who already resolved the client-role fact hand it in
    instead of paying for it again. The floor's whole query is one ``EXISTS`` over
    ``membership_roles`` — the same join the membership statement already carries a
    ``bool_or`` for — so on the request path it was a pure duplicate (§15's note that the two
    answers are resolved separately and must agree; this is how they agree by construction
    rather than by coincidence).
    """
    scopes: list[frozenset[uuid.UUID]] = []
    sources: set[str] = set()
    for key, resolver in _resolvers.items():
        if key == SCOPE_SOURCE_CLIENT_ROLE and holds_client is not None:
            scope = _EMPTY if holds_client else None
        else:
            scope = await resolver(session, org_id, membership_id)
        if scope is None:
            continue
        scopes.append(scope)
        sources.add(key)
    if not scopes:
        return CompanyScopeResolution(None, frozenset())
    combined: frozenset[uuid.UUID] = frozenset()
    for scope in scopes:
        combined |= scope
    return CompanyScopeResolution(combined, frozenset(sources))


async def resolve_company_scope(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    """The membership's horizon: ``None`` = unrestricted, a set = only those companies.

    Every source consulted from scratch — for a caller answering *about* a membership rather
    than acting as one, which knows none of the facts up front.
    """
    return (await resolve_company_scope_details(session, org_id, membership_id)).scope
