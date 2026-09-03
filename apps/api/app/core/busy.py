"""The seam a scheduler reads "when is this person already taken" through (§6).

Planning a block onto a colleague's calendar needs one answer that three modules each hold a
third of: the tasks module knows their planned blocks, ``leave`` knows when they are away, and
the Google integration holds a mirror of whatever else is in their diary. A dialog that asked
each of them would have to import each of them, so the composition lives here — the
``app/core/tagmanager.py`` shape, applied to a calendar instead of a container.

**The permission travels with the provider, not with the borrower**, twice over (#365's rule).
Whether a caller may see *that the person is taken* is the borrower's question — the schedule
route answers it with ``tasks.schedule.write:any``, because being allowed to put something on
a calendar is exactly the reason to see what is already on it, which is also Google's own
free/busy rule. Whether the caller may see *what* is taking the time is each provider's own
read rule, and a provider that decides against it answers the interval **without** a title:
a colleague's Google appointment reads "bezet 10:00–11:00" and nothing more, just as it does in
Google Calendar itself, while their own reads its name. The interval is never withheld — an
unnamed block is honest, an invisible one is a double booking.

With a module disabled nothing is registered, and its third of the answer is simply absent —
the same as "nothing on that calendar", which is what every caller must already draw.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids a core → tenancy import cycle
    from app.core.tenancy import RequestContext

logger = logging.getLogger("schakl.busy")


@dataclass(frozen=True)
class BusyItem:
    """One stretch of somebody's time that is already spoken for, as a scheduler needs to draw it.

    Deliberately not the row behind it: a borrower gets a window, whose it is, and — only when
    the provider decided the caller may know — what it is.
    """

    user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    #: Which provider answered (``tasks.schedule``, ``leave``, ``google.calendar``) — the
    #: legend's key, and what lets a screen exclude the very block it is editing.
    source: str
    #: ``busy`` is a booking; ``away`` is an absence (leave), drawn as a band over the day rather
    #: than a block beside the others, because nothing can be planned around it.
    kind: str = "busy"
    all_day: bool = False
    #: A pending leave request, or a tentative invitation: still somebody's plan, drawn muted.
    tentative: bool = False
    #: ``None`` when the caller may see the time but not the reason — the free/busy answer.
    title: str | None = None
    #: The row's own id, present exactly when ``title`` is: it lets the editing dialog leave the
    #: block being moved out of its own conflict check, and is the one thing that never leaks
    #: without the title, since a caller who cannot read the row cannot use its id either.
    ref: str | None = None
    href: str | None = None


class BusyProvider(Protocol):
    async def __call__(
        self,
        ctx: RequestContext,
        user_ids: list[uuid.UUID],
        window_start: datetime,
        window_end: datetime,
    ) -> list[BusyItem]: ...


_providers: dict[str, BusyProvider] = {}


def register_busy_provider(key: str, provider: BusyProvider) -> None:
    """Called once by each contributing module/integration at import time."""
    _providers[key] = provider


def registered_busy_sources() -> list[str]:
    return sorted(_providers)


async def busy_items(
    ctx: RequestContext,
    user_ids: list[uuid.UUID],
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[BusyItem], list[str]]:
    """Everything the registered providers know about ``user_ids`` inside the window.

    Returns the items and the keys of the providers that **failed**. A source that raised is
    named rather than silently dropped: a calendar with one third missing looks exactly like a
    free afternoon, and the one thing a conflict check may never do is look complete when it is
    not (§17). The other providers' answers still stand — one broken mirror is not a reason to
    hide the leave everybody can see.
    """
    items: list[BusyItem] = []
    failed: list[str] = []
    for key, provider in sorted(_providers.items()):
        try:
            # Each provider inside its own SAVEPOINT (§18): a statement that fails poisons the
            # session for everything after it, and the next provider's answer is worth having.
            async with ctx.session.begin_nested():
                items.extend(await provider(ctx, user_ids, window_start, window_end))
        except Exception:  # noqa: BLE001 — see the docstring: named, never swallowed silently
            logger.warning("busy provider %s failed", key, exc_info=True)
            failed.append(key)
    items.sort(key=lambda item: (item.starts_at, item.ends_at))
    return items, failed
