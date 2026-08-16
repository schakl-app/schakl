"""Scheduled Timeon work. Business-licensed — see LICENSE.

One nightly job and one weekly prune. Three properties are worth stating.

**A cron run is a real run and says so.** It writes the same :class:`TimeonSyncRun` a button
does, with ``actor_user_id`` NULL — which is a different and useful answer from "an admin pressed
sync" when somebody is working out why a timesheet changed overnight.

**Only an account that asked for it runs.** ``auto_sync`` is off until a human has watched a dry
run and a real one; a nightly job that started the moment a key was pasted would make connecting
an irreversible act.

**One account's failure does not stop the next.** ``run_per_org`` already isolates tenants; this
isolates connections inside a tenant, because an agency with two organisations connected should
not lose both because one key expired.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_writable
from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.integrations.timeon.models import (
    SyncDirection,
    TimeonAccount,
    TimeonAccountStatus,
    TimeonSyncKind,
    TimeonSyncRun,
)
from app.integrations.timeon.sync import TimeonSyncService

logger = logging.getLogger("schakl.timeon")

#: Runs older than this are dropped. A run is an operational record, not an audit trail — the
#: trail of *what changed* lives on ``timeon_links`` and in the entries themselves — so keeping
#: three months is generous and keeping three years is a table that only grows.
RUN_RETENTION_DAYS = 90


async def timeon_nightly(_ctx: dict) -> None:
    """Sync every connection that asked to be synced.

    04:20 — clear of the platform's 04:00/04:40/05:00 jobs, and after midnight in every European
    zone so "yesterday's hours" means yesterday.
    """
    if not await sku_writable("timeon"):
        # Past expiry the integration is read-only (the mount gate answers 402 on every
        # mutation), and a nightly job that wrote anyway would be the one path around it.
        return
    await run_per_org(_sync_org)


async def _sync_org(org: Org, session: AsyncSession) -> None:
    ctx = system_context(org, session)
    accounts = (
        (
            await session.execute(
                select(TimeonAccount)
                .where(TimeonAccount.org_id == org.id)
                .where(TimeonAccount.active.is_(True))
                .where(TimeonAccount.auto_sync.is_(True))
                .where(TimeonAccount.status == TimeonAccountStatus.ACTIVE.value)
            )
        )
        .scalars()
        .all()
    )
    for account in accounts:
        if (
            account.hours_direction == SyncDirection.OFF.value
            and account.projects_direction == SyncDirection.OFF.value
        ):
            # Auto-sync on with both directions off is a tenant who turned the switches off and
            # left the schedule on. Reading Timeon nightly to do nothing with the answer is a
            # rate-limit spent on a decision already made.
            continue
        try:
            await TimeonSyncService(ctx, account).run(
                kind=TimeonSyncKind.FULL, dry_run=False, actor_user_id=None
            )
        except Exception:  # noqa: BLE001 - one connection's failure is not the next one's
            logger.exception("timeon nightly sync failed for account %s", account.id)
            await session.rollback()


async def timeon_prune_runs(_ctx: dict) -> None:
    """Drop run records past :data:`RUN_RETENTION_DAYS`."""
    from datetime import UTC, datetime, timedelta

    cutoff = datetime.now(UTC) - timedelta(days=RUN_RETENTION_DAYS)

    async def prune(org: Org, session: AsyncSession) -> None:
        rows = (
            (
                await session.execute(
                    select(TimeonSyncRun)
                    .where(TimeonSyncRun.org_id == org.id)
                    .where(TimeonSyncRun.created_at < cutoff)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            await session.delete(row)

    await run_per_org(prune)
