"""ARQ entry points for the automation module (issue #27).

``automation_execute_run`` is the on-demand job the trigger handler enqueues (the first
non-cron job in the platform; the worker learns about it via the descriptor's
``worker_functions``). ``requeue_stale_runs`` is the safety net: a run whose enqueue was lost
(Redis blip) or whose enqueue raced the emitter's rollback window stays ``pending`` — the
sweep re-offers anything pending for over two minutes. Re-offering is safe because the
executor's claim (``pending → running``) is the real gate; a run the first delivery already
claimed simply isn't claimable twice.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.models import Org
from app.modules.automation import queue
from app.modules.automation.executor import execute_run
from app.modules.automation.models import RUN_PENDING, AutomationRun

#: A pending run older than this missed its enqueue (the initial defer is 2 s).
STALE_AFTER = timedelta(minutes=2)


async def automation_execute_run(ctx: dict, org_id: str, run_id: str) -> str:
    """Execute one queued run. Never raises — a rule's failure is a run status, not a crash."""
    return await execute_run(uuid.UUID(org_id), uuid.UUID(run_id))


async def requeue_stale_runs(ctx: dict) -> int:
    """Cron sweep: re-offer pending runs that never reached the worker, per org."""
    from app.core.jobs import run_per_org

    total = 0

    async def _per_org(org: Org, session: AsyncSession) -> None:
        nonlocal total
        cutoff = datetime.now(UTC) - STALE_AFTER
        stale = (
            (
                await session.execute(
                    select(AutomationRun.id).where(
                        AutomationRun.org_id == org.id,
                        AutomationRun.status == RUN_PENDING,
                        AutomationRun.created_at < cutoff,
                    )
                )
            )
            .scalars()
            .all()
        )
        for run_id in stale:
            if await queue.enqueue_run(org.id, run_id, requeue=True):
                total += 1

    await run_per_org(_per_org)
    return total
