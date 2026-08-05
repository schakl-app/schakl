"""Minimal in-process event bus for cross-module triggers (CLAUDE.md §6).

Modules never import each other's internals (Golden Rule 3), but some need to react to
another module's writes — e.g. the tasks module instantiates onboarding templates when a
company enters a lifecycle status, and the notifications module fans an event out to the
people who care. The emitting service calls :func:`emit`; interested modules :func:`subscribe`
a handler in their package ``__init__``.

Handlers run inline, in the caller's request transaction: an exception propagates and rolls
the whole write back, so an emitted event and its side effects commit atomically. Keep the
event surface deliberately tiny; today the companies/tasks/projects/leave/time modules emit
(company/task/project/leave/time events) and notifications subscribes them all.

An event carries an :class:`EmitContext` — the tenant, the session, and the actor. A request
handler passes its ``RequestContext``; a background job (ARQ cron) has no request, so it wraps
the ``(org, session)`` ``run_per_org`` hands it in a :class:`SystemContext` (``user=None`` ⇒
the system is the actor). Both satisfy ``EmitContext`` structurally, so a cron emits through
the same bus as a request.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.auth.models import User
    from app.core.models import Org


class EmitContext(Protocol):
    """What an event handler needs: a bound tenant, its session, and the acting user.

    Both a request-scoped ``RequestContext`` and a background ``SystemContext`` conform, so
    the same handler serves a live request and a cron tick without inventing a fake request.
    """

    org: Any
    session: Any
    user: Any


@dataclass
class SystemContext:
    """Emit context for background work (ARQ cron): a bound tenant + session, no user.

    ``run_per_org`` hands a job ``(org, session)`` with the RLS GUC already bound to that org;
    wrapping them here lets a cron ``emit`` through the same bus as a request, with
    ``user=None`` marking the actor as the system (never a person to exclude from fan-out).

    It also exposes ``repo`` so a job can call another module's **read-only** published service
    the same way a request does. Anything that reads ``ctx.user`` is a request
    concern and will rightly fail here: a cron has no one to authorize.

    Since #300 it also answers the three questions a *service* asks of its context, so a job
    can drive one rather than re-implementing it:

    * ``can`` / ``require`` — **always yes.** Not a hole: there is no principal here to
      authorize, and inventing a permissive "system user" would put a real membership's name
      on work nobody did. What gates a job is the job — its licence check and its schedule —
      and it is bounded by ``run_per_org`` binding exactly one org's RLS.
    * ``company_scope`` / ``is_portal`` — a cron is not a membership and never a client login,
      so: unrestricted, and never external. Stated rather than left to ``getattr``, because a
      service reading ``is_portal`` off a context that has none would silently take the staff
      branch and be right only by accident.
    * ``release_db`` — a no-op window. A request holds one pooled connection for its whole
      transaction, which is why releasing it around a slow call matters there; a worker owns
      its session outright and has nothing to hand back. Committing here instead would end a
      job's transaction halfway through its own work.
    """

    org: Org
    session: AsyncSession
    user: User | None = None
    #: A cron sees its whole org: it is not a membership, so no company horizon applies.
    company_scope: frozenset[uuid.UUID] | None = None
    is_portal: bool = False

    def repo(self, model: type[Any]) -> Any:
        from app.core.tenancy import TenantScopedRepository

        return TenantScopedRepository(self.session, self.org.id, model)

    def can(self, permission: str, scope: str | None = None) -> bool:  # noqa: ARG002
        return True

    def require(self, permission: str, scope: str | None = None) -> None:
        """No-op: see the class docstring. A job is gated by being a job."""

    @asynccontextmanager
    async def release_db(self) -> AsyncGenerator[None, None]:
        yield


EventHandler = Callable[["EmitContext", dict[str, Any]], Awaitable[None]]

_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event: str, handler: EventHandler) -> None:
    _handlers.setdefault(event, []).append(handler)


async def emit(event: str, ctx: EmitContext, payload: dict[str, Any]) -> None:
    for handler in _handlers.get(event, []):
        await handler(ctx, payload)
