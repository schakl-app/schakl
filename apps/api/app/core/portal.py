"""Portal-membership introspection seam (issue #193).

A portal login is an ordinary membership whose user is linked to a *contact* — a fact owned
by the contacts module. Other modules (notification fan-out, pickers) must be able to ask
"which of these users are portal logins?" without importing the contacts module's internals
(CLAUDE.md §6), so the module registers the answerer here, exactly like the company-scope
resolver seam (``app/core/scope.py``). No module registered = no portal users.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

PortalUserResolver = Callable[
    [AsyncSession, uuid.UUID, set[uuid.UUID]], Awaitable[set[uuid.UUID]]
]

_resolver: PortalUserResolver | None = None


def register_portal_user_resolver(resolver: PortalUserResolver) -> None:
    """Called once by the contacts module's package ``__init__``."""
    global _resolver
    _resolver = resolver


async def portal_user_ids(
    session: AsyncSession, org_id: uuid.UUID, candidates: set[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of ``candidates`` are portal logins in this org. Empty when none (or when the
    contacts module is disabled — then no portal logins can exist either)."""
    if _resolver is None or not candidates:
        return set()
    return await _resolver(session, org_id, candidates)
