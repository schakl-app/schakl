"""Minimal in-process event bus for cross-module triggers (CLAUDE.md §6).

Modules never import each other's internals (Golden Rule 3), but some need to react to
another module's writes — e.g. the tasks module instantiates onboarding templates when a
company enters a lifecycle status. The emitting service calls :func:`emit`; interested
modules :func:`subscribe` a handler in their package ``__init__``.

Handlers run inline, in the caller's request transaction: an exception propagates and rolls
the whole write back, so an emitted event and its side effects commit atomically. Keep the
event surface deliberately tiny; today only the companies module emits:

- ``company.created``        payload ``{company_id, status}``
- ``company.status_changed`` payload ``{company_id, status, previous_status}``
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.tenancy import RequestContext

EventHandler = Callable[["RequestContext", dict[str, Any]], Awaitable[None]]

_handlers: dict[str, list[EventHandler]] = {}


def subscribe(event: str, handler: EventHandler) -> None:
    _handlers.setdefault(event, []).append(handler)


async def emit(event: str, ctx: RequestContext, payload: dict[str, Any]) -> None:
    for handler in _handlers.get(event, []):
        await handler(ctx, payload)
