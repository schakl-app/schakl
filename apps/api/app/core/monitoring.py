"""Is this website up? — the one question ``websites`` asks about a module it may not name.

``websites`` stores ``uptime_enabled``, which is a *configuration flag*: somebody ticked a box.
The list drew a green pill from it (#356), and green is this product's healthy state — so a site
that had been down for two hours rendered exactly like one that was up, because the only input
was the tick. The real answer lives one module over, in ``uptime``'s heartbeat window, and §6
forbids ``websites`` importing it: the module may not even be enabled.

So the question crosses at a seam, the same way a register's presence does
(:mod:`app.core.registrar.presence`) and a cross-module row reference does
(:mod:`app.core.directory`). The owner registers a resolver built from *its own* models; core
holds the protocol and the composition and learns nothing about the far side's tables.

Three states, and they are not interchangeable:

* ``"up"`` / ``"down"`` / ``"pending"`` / ``"maintenance"`` — the monitor's own vocabulary, last
  observed. Something looked, and this is what it saw.
* ``None`` with a monitor attached — monitored, never observed. Not the same as *down*, and a
  screen that paints them alike is making the mistake this module exists to stop.
* absent from the mapping — no monitor at all. The site is not watched; nothing is claimed.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

#: ``(ctx, website_ids) -> {website_id: status | None}``.
#:
#: A key present with ``None`` means *watched, never observed*; a key absent means *not watched*.
#: Batched by construction — it takes the whole page's ids — because a per-row resolver is the
#: N+1 ``docs/PERFORMANCE.md`` bans, and a list is exactly where this is read.
WebsiteStatusResolver = Callable[[Any, set[uuid.UUID]], Awaitable[dict[uuid.UUID, str | None]]]

_RESOLVER: WebsiteStatusResolver | None = None


def register_website_status_resolver(resolver: WebsiteStatusResolver) -> None:
    """Register the module that can answer. Re-registering a *different* resolver is a
    programming error rather than a silent replacement (``register_presence``'s rule)."""
    global _RESOLVER
    if _RESOLVER is not None and _RESOLVER is not resolver:
        raise ValueError("a website status resolver is already registered")
    _RESOLVER = resolver


async def website_statuses(ctx: Any, website_ids: set[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    """Last observed status per website. Empty when no module answers — which is a real answer:
    nothing is watching, so nothing is claimed."""
    if _RESOLVER is None or not website_ids:
        return {}
    return await _RESOLVER(ctx, website_ids)
