"""ARQ worker (CLAUDE.md §3).

P0 ships a trivial task to prove the Redis + ARQ wiring end-to-end; real jobs (email, PDF,
Google sync, scheduled reports) arrive in later phases. Run with:
    arq app.worker.WorkerSettings
"""

from __future__ import annotations

import logging

from arq.connections import RedisSettings

from app.config import settings

logger = logging.getLogger("vlotr.worker")


async def heartbeat(ctx: dict) -> str:
    logger.info("worker heartbeat")
    return "ok"


async def startup(ctx: dict) -> None:
    logger.info("vlotr worker started")


class WorkerSettings:
    functions = [heartbeat]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
