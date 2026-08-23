"""Scheduled Timeon work. Business-licensed — see LICENSE.

One tick and one weekly prune. Five properties are worth stating.

**A cron run is a real run and says so.** It writes the same :class:`TimeonSyncRun` a button
does, with ``actor_user_id`` NULL — which is a different and useful answer from "an admin pressed
sync" when somebody is working out why a timesheet changed overnight.

**Only an account that asked for it runs.** ``auto_sync`` is off until a human has watched a dry
run and a real one; a job that started the moment a key was pasted would make connecting an
irreversible act.

**The tick is not the schedule** (#388). It fires every quarter of an hour and each account
decides whether *its* moment has come (:mod:`app.integrations.timeon.schedule`), because one ARQ
cron cannot say "hourly for this connection and nightly for that one" — and because a schedule
belongs to the tenant running the cutover, not to a constant in our source.

**The entitlement question is asked of the authority that exists** (#387). This job used to call
``sku_writable("timeon")`` with no org and no host, which on the **cloud** posture asks the
*instance* licence — the one authority a cloud tenant does not have, because the operator runs
the installation and the tenant buys a plan (``core/entitlements``). So an org on ``unlimited``,
entitled to everything, was refused by a gate that never got to ask about it, and breik.'s nightly
returned in twenty milliseconds every night for five nights without leaving a trace anywhere.
:func:`sku_cron_enabled` is the answer every other cron in this codebase already used: the
instance licence self-hosted, and on cloud a deferral to ``run_per_org``, which filters the orgs
whose plan has lapsed one at a time.

**One account's failure does not stop the next, and does not undo it either.** ``run_per_org``
isolates tenants; this isolates connections inside a tenant — with a **commit per account**,
because the rollback that contains a failure would otherwise also discard the hours the previous
account synced successfully. The RLS GUC is transaction-local (``app.db.set_current_org``), so it
is re-bound after every commit; forgetting that is a session that quietly reads nothing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.core.timezone import org_today, org_zoneinfo
from app.db import set_current_org
from app.integrations.timeon.models import (
    SyncDirection,
    TimeonAccount,
    TimeonAccountStatus,
    TimeonSyncKind,
    TimeonSyncRun,
)
from app.integrations.timeon.schedule import catch_up_days, is_due
from app.integrations.timeon.sync import TimeonSyncService

logger = logging.getLogger("schakl.timeon")

#: Runs older than this are dropped. A run is an operational record, not an audit trail — the
#: trail of *what changed* lives on ``timeon_links`` and in the entries themselves — so keeping
#: three months is generous and keeping three years is a table that only grows.
RUN_RETENTION_DAYS = 90


async def timeon_tick(_ctx: dict) -> None:
    """Sync every connection whose scheduled moment has come.

    Quarter-hourly. Most ticks decide nothing and cost two queries per org; the account's own
    ``auto_frequency``/``auto_time`` decide when one actually runs.
    """
    if not await sku_cron_enabled("timeon"):
        # Past expiry the integration is read-only (the mount gate answers 402 on every
        # mutation), and a scheduled job that wrote anyway would be the one path around it.
        # Said out loud, because a job that decides not to run and leaves no trace is exactly
        # how #387 survived five nights: silence and "nothing changed in Timeon" look identical.
        logger.info("timeon: scheduled sync skipped — the 'timeon' licence is not writable")
        return
    await run_per_org(_sync_org)


async def _sync_org(org: Org, session: AsyncSession) -> None:
    now = datetime.now(UTC)
    zone = await org_zoneinfo(session, org.id)
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
        if not is_due(
            frequency=account.auto_frequency,
            interval_hours=account.auto_interval_hours,
            at=account.auto_time,
            last_run=account.last_auto_run_at,
            zone=zone,
            now=now,
        ):
            continue

        days = catch_up_days(
            frequency=account.auto_frequency, last_run=account.last_auto_run_at, zone=zone, now=now
        )
        window_from = (
            None if days is None else (await org_today(session, org.id)) - timedelta(days=days)
        )
        # Stamped **before** the run and committed with it, so a connection whose credential has
        # lapsed is retried on its own cadence rather than on every tick for the rest of the week.
        account.last_auto_run_at = now
        await session.commit()
        await set_current_org(session, org.id)  # the GUC is transaction-local

        ctx = system_context(org, session)
        try:
            run = await TimeonSyncService(ctx, account).run(
                kind=TimeonSyncKind.FULL,
                dry_run=False,
                window_from=window_from,
                actor_user_id=None,
            )
            await session.commit()
            logger.info(
                "timeon: scheduled sync for account %s finished ok=%s counts=%s",
                account.id,
                run.ok,
                dict(run.counts or {}),
            )
        except Exception:  # noqa: BLE001 - one connection's failure is not the next one's
            logger.exception("timeon scheduled sync failed for account %s", account.id)
            await session.rollback()
        await set_current_org(session, org.id)


async def timeon_prune_runs(_ctx: dict) -> None:
    """Drop run records past :data:`RUN_RETENTION_DAYS`."""
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
