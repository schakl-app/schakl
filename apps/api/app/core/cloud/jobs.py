"""Cloud cron jobs (epic #199). Business-licensed — see this directory's LICENSE.

Both are **instance-wide** (they read/write ``orgs``, an instance-level table), so they
deliberately do not ride ``run_per_org``. Failures are logged and swallowed — a cron crash
loop on a cloud box helps nobody.
"""

from __future__ import annotations

import logging

from app.core.cloud.domain_health import sweep_domain_health
from app.core.cloud.ingress import sync_ingress
from app.core.cloud.lifecycle import sweep
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


async def cloud_lifecycle_sweep(_ctx: dict | None = None) -> dict[str, int]:
    """Daily: advance every org that carries an end date (#199).

    Orgs with ``ends_at IS NULL`` — the default, and every self-host org — are never looked at.
    Off unless ``SCHAKL_CLOUD_LIFECYCLE_ENABLED``; the purge additionally needs
    ``SCHAKL_CLOUD_LIFECYCLE_DESTRUCTIVE``, so the first deployment can warn and suspend for
    real while nothing is destroyed.
    """
    try:
        async with async_session_maker() as session:
            counts = await sweep(session)
        if any(counts.values()):
            logger.info(
                "lifecycle sweep: %d warned, %d suspended, %d terminated",
                counts["warned"], counts["suspended"], counts["terminated"],
            )
        return counts
    except Exception:  # noqa: BLE001 — cron contract: log, never crash-loop
        logger.exception("lifecycle sweep failed")
        return {"warned": 0, "suspended": 0, "terminated": 0}


async def cloud_sync_ingress(_ctx: dict | None = None) -> str | None:
    """Daily drift guard for the custom-domain ingress fragment (#202); the request-time
    hooks in the domain claim/verify flow do the timely writes."""
    async with async_session_maker() as session:
        path = await sync_ingress(session)
    return str(path) if path else None


async def cloud_domains_sweep(_ctx: dict | None = None) -> dict[str, int]:
    """Daily: reconcile every Cloudflare-managed custom domain's hostname/certificate/DNS
    state and alert the org's domain managers on new problems (#291). The safety sweep —
    the settings page's "check now" endpoint does the interactive refreshes."""
    try:
        async with async_session_maker() as session:
            counts = await sweep_domain_health(session)
            await session.commit()
        if counts["alerted"]:
            logger.info(
                "domain sweep: %d checked, %d alerted", counts["checked"], counts["alerted"]
            )
        return counts
    except Exception:  # noqa: BLE001 — cron contract: log, never crash-loop
        logger.exception("domain health sweep failed")
        return {"checked": 0, "alerted": 0}
