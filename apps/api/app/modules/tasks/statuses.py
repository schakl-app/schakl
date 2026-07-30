"""Tenant-configurable task statuses (issue #62): defaults, seeding, and category helpers.

``Task.status`` used to be a closed ``TaskStatus`` enum; it is now a ``key`` into the per-org
``task_statuses`` vocabulary. This module is the one place that seeds an org's defaults and answers
the three questions the rest of the code used to hardcode: *what status does a new task start in*
(``is_default``), *which statuses mean finished* (``is_terminal``), and *what is the board/sort
order* (``position``). Kept out of ``service.py`` so the cron jobs and the company panel can reuse
it without importing the whole service.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.tasks.models import TaskStatusDef


@dataclass(frozen=True)
class _StatusSeed:
    key: str
    name: str
    color: str
    position: int
    is_terminal: bool
    is_default: bool


# The vocabulary every org starts with — the old hardcoded ``open`` / ``in_progress`` / ``done``,
# so a fresh install behaves exactly as before. Dutch names (the default UI language); a tenant
# renames, reorders, recolours or extends them under Settings. ``done`` is the terminal state and
# ``open`` is where new tasks land.
# Colors are shared palette *tokens* (app/core/ui/colors.ts — sky/amber/green…), not hex, so the
# web renders a status chip with the same classes it uses for labels and leave types.
DEFAULT_STATUSES: tuple[_StatusSeed, ...] = (
    _StatusSeed("open", "Open", "sky", 0, is_terminal=False, is_default=True),
    _StatusSeed("in_progress", "In behandeling", "amber", 1, is_terminal=False, is_default=False),
    _StatusSeed("done", "Klaar", "green", 2, is_terminal=True, is_default=False),
)


def _ordered(org_id: uuid.UUID):
    return (
        select(TaskStatusDef)
        .where(TaskStatusDef.org_id == org_id)
        .order_by(TaskStatusDef.position.asc(), TaskStatusDef.key.asc())
    )


async def _seed(session: AsyncSession, org_id: uuid.UUID) -> list[TaskStatusDef]:
    """Write the default vocabulary and return it in board order.

    Callers must already know the org has none — the "are there any?" probe lives with them,
    because the read that answers it is the same read that wants the rows.
    """
    rows = [
        TaskStatusDef(
            org_id=org_id,
            key=seed.key,
            name=seed.name,
            color=seed.color,
            position=seed.position,
            is_terminal=seed.is_terminal,
            is_default=seed.is_default,
        )
        for seed in DEFAULT_STATUSES
    ]
    session.add_all(rows)
    await session.flush()
    return rows


async def ensure_statuses(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Seed the default statuses for an org that has none yet (idempotent).

    Runs lazily on the first read, so a brand-new org from the first-run wizard and every
    already-existing org both get the vocabulary without a migration or a boot-time job. The
    ``(org_id, key)`` unique constraint makes a concurrent double-seed a no-op, not a duplicate.
    """
    exists = await session.scalar(
        select(TaskStatusDef.id).where(TaskStatusDef.org_id == org_id).limit(1)
    )
    if exists is None:
        await _seed(session, org_id)


async def load_statuses(session: AsyncSession, org_id: uuid.UUID) -> list[TaskStatusDef]:
    """The org's statuses in board/sort order, seeding the defaults if there are none.

    **One statement on the hot path.** Every task list, board, group aggregate and company panel
    starts here, and the old "does this org have any?" probe asked a question the ordered read
    was about to answer anyway — a second round-trip on every one of those requests, forever,
    to cover the single request in an org's life that finds nothing (docs/PERFORMANCE.md).
    Seeding now hangs off the empty result instead of preceding it.
    """
    statuses = list((await session.execute(_ordered(org_id))).scalars())
    if statuses:
        return statuses
    # DEFAULT_STATUSES is already in board order, so the seeded rows need no re-read.
    return await _seed(session, org_id)


def status_order(statuses: list[TaskStatusDef]) -> list[str]:
    return [s.key for s in statuses]


def default_key(statuses: list[TaskStatusDef]) -> str:
    """The key a new task starts in: the ``is_default`` status, else the first in order."""
    for s in statuses:
        if s.is_default:
            return s.key
    return statuses[0].key if statuses else "open"


def terminal_keys(statuses: list[TaskStatusDef]) -> set[str]:
    return {s.key for s in statuses if s.is_terminal}


def non_terminal_keys(statuses: list[TaskStatusDef]) -> set[str]:
    return {s.key for s in statuses if not s.is_terminal}
