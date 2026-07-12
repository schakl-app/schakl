"""ARQ worker (CLAUDE.md §3).

Loads the enabled modules (same discovery as ``main.py``) and collects the cron jobs each
module contributes via its :class:`ModuleDescriptor`, plus the core jobs below.

Note: ARQ cron schedules fire in UTC; jobs that reason about "today" must compute the date
in the tenant-relevant timezone (``Europe/Amsterdam``) themselves.
"""

from __future__ import annotations

import importlib
import logging
from datetime import UTC, datetime

from arq import cron
from arq.connections import RedisSettings

from app.config import settings
from app.core.apikeys.jobs import flush_api_key_last_used
from app.core.cache import WORKER_HEARTBEAT_KEY, WORKER_HEARTBEAT_TTL, get_redis
from app.core.update_check import check_for_update
from app.registry import registry

logger = logging.getLogger("schakl.worker")


def _collect_cron_jobs() -> list:
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")
    jobs: list = []
    for module in registry.enabled(settings.enabled_modules):
        jobs.extend(module.cron_jobs)
    return jobs


def _collect_functions() -> list:
    """One-off job functions modules contribute — enqueued from the API by name (#125)."""
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")
    functions: list = []
    for module in registry.enabled(settings.enabled_modules):
        functions.extend(module.worker_functions)
    return functions


async def heartbeat(ctx: dict) -> str:
    """Record that this worker is alive (issue #18).

    Without it a dead worker is invisible — the API keeps serving and jobs silently pile up.
    The key carries a TTL, so ``system/info`` reads liveness from its presence rather than
    from parsing arq's internal health string.
    """
    now = datetime.now(UTC).isoformat()
    await get_redis().set(WORKER_HEARTBEAT_KEY, now, ex=WORKER_HEARTBEAT_TTL)
    return now


async def startup(ctx: dict) -> None:
    logger.info("schakl worker started (version %s)", settings.version)
    # Don't wait up to a minute for the first cron tick to declare ourselves alive.
    await heartbeat(ctx)


#: Core cron jobs, contributed by the platform rather than by a domain module.
_CORE_CRON_JOBS = [
    # Every minute, on the minute.
    cron(heartbeat, second=0, run_at_startup=False),
    # Daily. Off-peak, and offset from the tasks module's 04:00 recurrence spawn.
    cron(check_for_update, hour=5, minute=0),
    # Drain the API-key last-use buffer to the DB every few minutes (#20) — off the hot path.
    cron(flush_api_key_last_used, minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55}),
]


class WorkerSettings:
    functions = [heartbeat] + _collect_functions()
    cron_jobs = _CORE_CRON_JOBS + _collect_cron_jobs()
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
