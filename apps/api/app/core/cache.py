"""Shared Redis handle for the API process (CLAUDE.md §3).

The ARQ worker gets its own client from ``RedisSettings``; request handlers that need Redis
(today: the system-info readiness probe and the cached update check) share this lazily-created
one. ``decode_responses=True`` so callers get ``str``, matching what the worker writes.
"""

from __future__ import annotations

from redis.asyncio import Redis

from app.config import settings

_client: Redis | None = None


def get_redis() -> Redis:
    """The process-wide Redis client. Connections are pooled and opened on first use."""
    global _client
    if _client is None:
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


# --- Keys owned by the app (arq owns everything under `arq:`) ----------------------------- #

#: ISO-8601 UTC timestamp, rewritten every minute by the worker's heartbeat cron.
#: Absent (TTL expired) ⇒ no worker has checked in recently.
WORKER_HEARTBEAT_KEY = "schakl:worker:heartbeat"
#: How long a heartbeat stays valid. Comfortably longer than the once-a-minute cron so a
#: single missed tick (a slow job hogging the loop) doesn't read as a dead worker.
WORKER_HEARTBEAT_TTL = 180

#: JSON blob written by the daily update check: {latest, release_url, checked_at}.
UPDATE_CHECK_KEY = "schakl:update:latest"
