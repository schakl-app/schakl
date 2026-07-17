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

from sqlalchemy.ext.asyncio import AsyncSession

CompanyScopeResolver = Callable[
    [AsyncSession, uuid.UUID, uuid.UUID], Awaitable[frozenset[uuid.UUID] | None]
]

_resolvers: list[CompanyScopeResolver] = []


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
