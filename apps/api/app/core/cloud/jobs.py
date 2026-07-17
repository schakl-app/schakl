"""Cloud cron jobs (epic #199). Business-licensed — see this directory's LICENSE.

Both are **instance-wide** (they read/write ``orgs``, an instance-level table), so they
deliberately do not ride ``run_per_org``. Failures are logged and swallowed — a cron crash
loop on a cloud box helps nobody.
"""

from __future__ import annotations

import logging

from app.core.cloud.ingress import sync_ingress
from app.core.cloud.plans import suspend_expired_trials
from app.db import async_session_maker

logger = logging.getLogger(__name__)


async def cloud_expire_trials(_ctx: dict | None = None) -> int:
    """Daily: suspend active orgs whose trial ran out (plan="trial" only — "standard" is
    billing-managed and "unlimited" never expires)."""
    try:
        async with async_session_maker() as session:
            count = await suspend_expired_trials(session)
            await session.commit()
        if count:
            logger.info("suspended %d org(s) with expired trials", count)
        return count
    except Exception:  # noqa: BLE001 — cron contract: log, never crash-loop
        logger.exception("trial expiry sweep failed")
        return 0


async def cloud_sync_ingress(_ctx: dict | None = None) -> str | None:
    """Daily drift guard for the custom-domain ingress fragment (#202); the request-time
    hooks in the domain claim/verify flow do the timely writes."""
    async with async_session_maker() as session:
        path = await sync_ingress(session)
    return str(path) if path else None
