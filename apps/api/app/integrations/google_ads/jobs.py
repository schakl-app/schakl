"""ARQ jobs for the Google Ads mirror. Business-licensed — see LICENSE.

- ``google_ads_sync_all`` — the nightly cron, fanned out per active org via ``run_per_org`` (the
  RLS GUC bound per tenant, one transaction each). It re-pulls a trailing window for every linked
  account and upserts.
- ``google_ads_backfill_account`` — a one-off thirteen-month fill, enqueued when an account is
  first linked, so a year-over-year comparison works the day after rather than a year after.

It runs at **05:15**, deliberately after ``marketing``'s 04:45: both walk every org and both make
outbound Google calls, and stacking them on the same minute is how a self-hosted box with thirty
clients discovers its own rate limits at four in the morning.
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

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
                # row, and a re-link or a reconnect is what fixes it.
                logger.info(
                    "google ads: backfill halted for account %s after %s days", account_id, offset
                )
                return


def backfill_delay() -> timedelta:
    """Deferred a little, so the create's own transaction has committed before the job reads it."""
    return timedelta(seconds=5)
