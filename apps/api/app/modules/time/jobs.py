"""Retention for autosaved time-entry drafts (#44).

A draft untouched for 30 days is abandoned keystrokes, not a record — purged by a daily ARQ
cron, per org via ``run_per_org`` (RLS GUC bound per tenant). The horizon is a constant, not a
tenant setting, by design.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org
from app.core.models import Org
from app.modules.time.models import TimeEntryDraft

logger = logging.getLogger("schakl.time")

_DRAFT_RETENTION_DAYS = 30


async def _purge_org_drafts(org: Org, session: AsyncSession) -> None:
    cutoff = datetime.now(UTC) - timedelta(days=_DRAFT_RETENTION_DAYS)
    result = await session.execute(
        delete(TimeEntryDraft).where(
            TimeEntryDraft.org_id == org.id, TimeEntryDraft.updated_at < cutoff
        )
    )
    if result.rowcount:
        logger.info("purged %s stale time-entry drafts in org %s", result.rowcount, org.slug)


async def purge_stale_time_drafts(ctx: dict) -> None:
    """ARQ entrypoint: drop drafts untouched for 30 days (#44)."""
    from app.core.entitlements.service import license_state

    # Licensed module (issue #137): past expiry the module is read-only, and read-only means
    # the background half does not delete data either — drafts simply wait for a renewal.
    if not (await license_state()).writable("time"):
        return
    await run_per_org(_purge_org_drafts)
