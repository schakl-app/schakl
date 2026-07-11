"""Flush API-key last-use markers from Redis to the database (#20).

Auth records last-use in a Redis hash (``schakl:apikey:lastused``) rather than writing the DB on
every request (docs/PERFORMANCE.md). This cron drains that hash, binding RLS per org before each
update. Approximate by design: a mark that races the drain is simply picked up next run.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import update

from app.core.apikeys.auth import _LAST_USED_KEY
from app.core.apikeys.models import ApiKey
from app.core.cache import get_redis
from app.db import async_session_maker, set_current_org

logger = logging.getLogger("schakl.apikeys")


async def flush_api_key_last_used(ctx: dict) -> int:
    """ARQ entrypoint: write buffered last-use timestamps to ``api_keys.last_used_at``."""
    redis = get_redis()
    marks = await redis.hgetall(_LAST_USED_KEY)
    if not marks:
        return 0

    by_org: dict[str, list[tuple[str, str]]] = {}
    for key_id, value in marks.items():
        org_id, _, iso = value.partition("|")
        if org_id and iso:
            by_org.setdefault(org_id, []).append((key_id, iso))

    updated = 0
    for org_id, items in by_org.items():
        async with async_session_maker() as session:
            await set_current_org(session, uuid.UUID(org_id))
            for key_id, iso in items:
                result = await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == uuid.UUID(key_id), ApiKey.org_id == uuid.UUID(org_id))
                    .values(last_used_at=datetime.fromisoformat(iso))
                )
                updated += result.rowcount or 0
            await session.commit()

    # Drop exactly the fields we drained; marks written since hgetall stay for the next run.
    await redis.hdel(_LAST_USED_KEY, *marks.keys())
    logger.debug("flushed %d api-key last-use timestamps", updated)
    return updated
