"""The ARQ enqueue seam (issue #27).

The platform's worker previously ran cron jobs only; this is the first *on-demand* job path.
The trigger handler calls :func:`enqueue_run` inside the emitter's transaction, so the call
must be quick and must never take the user's write down with it: a Redis outage logs, returns
``False``, and leaves the run row ``pending`` for the requeue cron to re-offer.

Tests never need a live worker: they stub this function (it is the one seam between the
request side and Redis) and call ``executor.execute_run`` directly in-process.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

from arq.connections import ArqRedis, RedisSettings, create_pool

from app.config import settings

logger = logging.getLogger("schakl.automation")

#: Give the emitter's transaction time to commit before the worker looks for the run row.
_DEFER = timedelta(seconds=2)

_pool: ArqRedis | None = None


async def get_pool() -> ArqRedis:
    global _pool  # noqa: PLW0603 - process-wide connection pool, created lazily
    if _pool is None:
        _pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    return _pool


async def enqueue_run(
    org_id: uuid.UUID, run_id: uuid.UUID, *, requeue: bool = False
) -> bool:
    """Offer one pending run to the worker. Best-effort; ``False`` = Redis unavailable.

    ``_job_id`` makes the first offer idempotent at the queue level too (a double enqueue is
    one job). A **requeue** deliberately drops it: ARQ keeps results per job id, so reusing
    the id would make the retry a silent no-op — the executor's status guard is what makes
    re-execution safe instead.
    """
    try:
        pool = await get_pool()
        await pool.enqueue_job(
            "automation_execute_run",
            str(org_id),
            str(run_id),
            _job_id=None if requeue else f"automation-run-{run_id}",
            _defer_by=_DEFER,
        )
        return True
    except Exception:
        logger.exception("automation: could not enqueue run %s (org %s)", run_id, org_id)
        return False
