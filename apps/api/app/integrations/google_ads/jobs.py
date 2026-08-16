"""ARQ jobs for the Google Ads mirror. Business-licensed — see LICENSE.

- ``google_ads_sync_all`` — the nightly cron, fanned out per active org via ``run_per_org`` (the
  RLS GUC bound per tenant, one transaction each). It re-pulls a trailing window for every linked
  account and upserts — and then queues the thirteen-month fill for any account that has never
  finished one.
- ``google_ads_backfill_account`` — the thirteen-month fill itself, so a year-over-year
  comparison works the day after an account is linked rather than a year after. Enqueued when an
  account is linked *and* by the nightly run above, because "we queued it once" and "it has run"
  are different facts and only the second one is worth acting on (#381): the enqueue at link
  time is best-effort by design, and thirteen accounts on the live instance were silently left
  holding a week of history each, which made every report for a past month print a Google Ads
  section of zeros.

It runs at **05:15**, deliberately after ``marketing``'s 04:45: both walk every org and both make
outbound Google calls, and stacking them on the same minute is how a self-hosted box with thirty
clients discovers its own rate limits at four in the morning.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.entitlements.service import sku_cron_enabled
from app.core.jobs import run_per_org
from app.core.models import Org, OrgStatus
from app.db import async_session_maker, set_current_org
from app.integrations.google_ads.models import GoogleAdsAccount
from app.integrations.google_ads.sync import (
    BACKFILL_DAYS,
    CHUNK_DAYS,
    accounts_to_sync,
    sync_account,
)

logger = logging.getLogger("schakl.googleads")


async def _licensed() -> bool:
    """The mount-time 402 covers requests; a cron writes on a schedule and needs its own check.

    Expired means **read-only, not gone**: the stored trend keeps rendering, and only the writing
    of new rows stops.
    """
    return await sku_cron_enabled("google_ads")


async def _sync_org(org: Org, session: AsyncSession) -> None:
    accounts = await accounts_to_sync(session, org)
    if not accounts:
        return
    failed = 0
    for account in accounts:
        # Each account swallows its own failure and records it on the row: one revoked grant
        # among twenty must not stop the other nineteen.
        if not await sync_account(session, org, account):
            failed += 1
    logger.info(
        "google ads: synced %s account(s) for org %s (%s failed)",
        len(accounts) - failed,
        org.slug,
        failed,
    )
    await _queue_missing_backfills(org, accounts)


async def _queue_missing_backfills(org: Org, accounts: list[GoogleAdsAccount]) -> None:
    """Any account whose thirteen months were never filled, queued again tonight (#381).

    The backfill was a one-off fired when an account is linked, and that enqueue is
    best-effort by design — its comment says *"a queue miss is not fatal, the nightly run
    catches up"*, which was true of nothing: the nightly run re-pulls a trailing week and has
    no opinion about the year behind it. Every account on the live instance was in exactly that
    state, so a report for any past month printed a Google Ads section of zeros.

    So the promise is now kept here. It is keyed on ``backfilled_at``, which is stamped only by
    a **complete** run, which gives three properties worth having: an account linked before this
    column existed is filled on the next nightly without anybody being told to press anything;
    a backfill that halts on a revoked grant is retried each night and costs one chunk until the
    grant is fixed; and a finished one is never asked again.

    Queued rather than run inline: this is thirteen chunked calls per account against a shared
    daily quota, and holding the nightly cron open for thirty of them would turn one slow
    account into a sync that never reaches the rest.
    """
    from app.core.jobs import enqueue

    for account in accounts:
        if account.backfilled_at is not None:
            continue
        try:
            await enqueue(
                "google_ads_backfill_account",
                str(org.id),
                str(account.id),
                _job_id=f"google-ads-backfill-{account.id}",
            )
        except Exception:  # noqa: BLE001 — the sync it rides on has already succeeded
            logger.warning("google ads: could not queue backfill for account %s", account.id)


async def google_ads_sync_all(ctx: dict) -> None:
    """Nightly entrypoint: re-pull the trailing window for every active org's accounts."""
    if not await _licensed():
        return
    await run_per_org(_sync_org)


async def google_ads_backfill_account(ctx: dict, org_id: str, account_id: str) -> None:
    """Fill thirteen months for one freshly linked account, a month at a time.

    Chunked and **committed per chunk**, so an interrupted backfill keeps what it managed. Each
    chunk re-binds the RLS GUC, because the GUC is transaction-local and the previous commit
    ended the transaction that carried it — the failure mode being that every chunk after the
    first silently reads and writes nothing.

    Finishing **stamps ``backfilled_at``**, which is the whole difference between a job that was
    queued once and a job that is known to have run (#381). Only a complete run stamps: a halt
    leaves the column NULL so the nightly sync asks again tomorrow, which is what turns a queue
    miss or a since-fixed credential into a self-healing state rather than a permanent hole
    nobody can see.
    """
    if not await _licensed():
        return
    async with async_session_maker() as session:
        org = await session.scalar(
            select(Org).where(
                Org.id == uuid.UUID(org_id), Org.status == OrgStatus.ACTIVE.value
            )
        )
        if org is None:
            return
        account: GoogleAdsAccount | None = None
        for offset in range(0, BACKFILL_DAYS, CHUNK_DAYS):
            await set_current_org(session, org.id)
            account = await session.scalar(
                select(GoogleAdsAccount).where(
                    GoogleAdsAccount.org_id == org.id,
                    GoogleAdsAccount.id == uuid.UUID(account_id),
                    GoogleAdsAccount.active.is_(True),
                )
            )
            if account is None:
                return
            # Each chunk asks for a window ending `offset` days ago. `sync_account` resolves the
            # end from the account's own clock, so the chunks tile the account's calendar rather
            # than the server's.
            days = min(CHUNK_DAYS, BACKFILL_DAYS - offset)
            ok = await sync_account(
                session, org, account, days=days, ends_days_ago=offset
            )
            await session.commit()
            if not ok:
                # Halt rather than grind through twelve more failing chunks: the error is on the
                # row, and a re-link or a reconnect is what fixes it. Deliberately **unstamped**,
                # so tonight's sync tries again.
                logger.info(
                    "google ads: backfill halted for account %s after %s days", account_id, offset
                )
                return
        if account is not None:
            # Re-bound because the last chunk's commit ended the transaction carrying the GUC —
            # the same trap the loop above documents, one statement past its last iteration.
            await set_current_org(session, org.id)
            account = await session.get(GoogleAdsAccount, account.id)
            if account is not None:
                account.backfilled_at = datetime.now(UTC)
                await session.commit()
                logger.info(
                    "google ads: backfilled %s days for account %s", BACKFILL_DAYS, account_id
                )


def backfill_delay() -> timedelta:
    """Deferred a little, so the create's own transaction has committed before the job reads it."""
    return timedelta(seconds=5)
