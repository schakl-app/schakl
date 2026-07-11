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
DEFAULT_STATUSES: tuple[_StatusSeed, ...] = (
    _StatusSeed("open", "Open", "#64748b", 0, is_terminal=False, is_default=True),
    _StatusSeed("in_progress", "In behandeling", "#3b82f6", 1, is_terminal=False, is_default=False),
    _StatusSeed("done", "Klaar", "#22c55e", 2, is_terminal=True, is_default=False),
)


async def ensure_statuses(session: AsyncSession, org_id: uuid.UUID) -> None:
    """Seed the default statuses for an org that has none yet (idempotent).

    Runs lazily on the first read, so a brand-new org from the first-run wizard and every
    already-existing org both get the vocabulary without a migration or a boot-time job. The
    ``(org_id, key)`` unique constraint makes a concurrent double-seed a no-op, not a duplicate.
    """
    exists = await session.scalar(
        select(TaskStatusDef.id).where(TaskStatusDef.org_id == org_id).limit(1)
    )
    if exists is not None:
        return
    for seed in DEFAULT_STATUSES:
        session.add(
            TaskStatusDef(
                org_id=org_id,
                key=seed.key,
                name=seed.name,
                color=seed.color,
                position=seed.position,
                is_terminal=seed.is_terminal,
                is_default=seed.is_default,
            )
        )
    await session.flush()


async def load_statuses(session: AsyncSession, org_id: uuid.UUID) -> list[TaskStatusDef]:
    """The org's statuses in board/sort order, seeding defaults first if there are none."""
    await ensure_statuses(session, org_id)
    return list(
        (
            await session.execute(
                select(TaskStatusDef)
                .where(TaskStatusDef.org_id == org_id)
                .order_by(TaskStatusDef.position.asc(), TaskStatusDef.key.asc())
            )
        ).scalars()
    )


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
