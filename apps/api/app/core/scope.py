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
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

if TYPE_CHECKING:  # ``tenancy`` imports this module, so the type is a forward reference only.
    from app.core.tenancy import RequestContext

CompanyScopeResolver = Callable[
    [AsyncSession, uuid.UUID, uuid.UUID], Awaitable[frozenset[uuid.UUID] | None]
]

_resolvers: list[CompanyScopeResolver] = []

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

    ``True`` for a type with no model behind it: ``avatar`` and ``hr_document`` are keyed by
    user id and have their own rules at the call site. Guessing here would silently gate them.
    """
    if ctx.company_scope is None:
        return True
    model = _horizon_entities.get(entity_type)
    if model is None:
        return True
    return await ctx.repo(model).get(entity_id) is not None


def register_company_scope_resolver(resolver: CompanyScopeResolver) -> None:
    """Called once per owning module's package ``__init__``. More than one source can bound
    a membership (company groups #191, a portal contact's companies #193); each resolver
    answers ``None`` for "this source doesn't restrict them"."""
    if resolver not in _resolvers:
        _resolvers.append(resolver)


async def resolve_company_scope(
    session: AsyncSession, org_id: uuid.UUID, membership_id: uuid.UUID
) -> frozenset[uuid.UUID] | None:
    """The membership's horizon: ``None`` = unrestricted, a set = only those companies.

    Restricting sources **union** (#193): a portal contact linked to two companies who is
    also assigned a group sees the union — while a membership no source restricts stays
    unrestricted. The union of restrictions can never widen past "everything", so combining
    with ``None`` (unrestricted) collapses to the restricted sets only.
    """
    scopes = [
        scope
        for resolver in _resolvers
        if (scope := await resolver(session, org_id, membership_id)) is not None
    ]
    if not scopes:
        return None
    combined: frozenset[uuid.UUID] = frozenset()
    for scope in scopes:
        combined |= scope
    return combined
