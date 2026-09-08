"""The nightly trash sweep — purge what the retention window has run out on (docs/TRASH.md).

Per org, per trashable entity, per row in its own SAVEPOINT: a row that refuses (it grew a
blocker since it was trashed, or a contributing module's purge hook failed) is logged and left
for the screen to explain, and the next row still goes. ``run_per_org`` commits per org, so one
tenant's failure never rolls back another's night.

Runs as the system (``system_context``): a purge has nobody behind it, and the trail's ``purged``
line therefore carries a NULL actor, which is what the trail means by "the system" (§16).
"""

from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.core.trash.service import TrashService, trash_specs
from app.errors import AppError

logger = logging.getLogger("schakl.trash")


async def trash_purge(ctx: dict) -> str:
    """The cron entry point."""
    purged = kept = 0

    async def per_org(org: Org, session: AsyncSession) -> None:
        nonlocal purged, kept
        service = TrashService(system_context(org, session))
        for spec in trash_specs().values():
            for entity_id in await service.due_for_purge(spec):
                try:
                    async with session.begin_nested():
                        await service.purge(spec.entity_type, entity_id)
                    purged += 1
                except AppError as exc:
                    kept += 1
                    logger.info(
                        "trash: kept %s %s in org %s past retention: %s %s",
                        spec.entity_type,
                        entity_id,
                        org.slug,
                        exc.message_key,
                        exc.details or "",
                    )

    await run_per_org(per_org)
    return f"trash purge: purged={purged} kept={kept}"
