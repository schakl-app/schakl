"""ARQ worker (CLAUDE.md §3).

Loads the enabled modules (same discovery as ``main.py``) and collects the cron jobs each
module contributes via its :class:`ModuleDescriptor`. Runs with:
    arq app.worker.WorkerSettings

Note: ARQ cron schedules fire in UTC; jobs that reason about "today" must compute the date
in the tenant-relevant timezone (``Europe/Amsterdam``) themselves.
"""

from __future__ import annotations

import importlib
import logging

from arq.connections import RedisSettings

from app.config import settings
from app.registry import registry

logger = logging.getLogger("vlotr.worker")


def _collect_cron_jobs() -> list:
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")
    jobs: list = []
    for module in registry.enabled(settings.enabled_modules):
        jobs.extend(module.cron_jobs)
    return jobs


async def heartbeat(ctx: dict) -> str:
    logger.info("worker heartbeat")
    return "ok"


async def startup(ctx: dict) -> None:
    logger.info("vlotr worker started")


class WorkerSettings:
    functions = [heartbeat]
    cron_jobs = _collect_cron_jobs()
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
