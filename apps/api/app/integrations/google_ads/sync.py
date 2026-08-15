"""The nightly mirror: daily aggregates and change history, stored so a trend costs no API call.

Business-licensed — see LICENSE.

**Every account syncs independently and swallows its own failure.** One revoked grant among
twenty must not stop the other nineteen, so a failure is recorded on the row
(``last_sync_error``) and the loop continues — the shape ``marketing``'s own sync already uses,
and the reason its nightly run is not one bad connection away from doing nothing.

Two things are re-pulled rather than appended, and both for the same reason: **a day read once
is a day read too early**. Ads conversions keep arriving for days after the click, and
``change_event`` is only queryable for 30 days, so the run re-reads a trailing window and
upserts. Overlap is the point, not waste.

The clock is the **account's**, not the org's. Google closes a campaign's day in the account's
own timezone, so "yesterday" for an account set to America/New_York is a different set of
impressions than yesterday in Europe/Amsterdam — and a sync that used the org's calendar would
store one account's Monday under another's Sunday.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.googleads import AdsError, AdsNotConfigured, describe_failure
from app.core.models import Org
from app.core.timezone import resolve_zoneinfo
from app.errors import AppError
from app.integrations.google_ads import reporting
from app.integrations.google_ads.models import (
    GoogleAdsAccount,
    GoogleAdsChange,
    GoogleAdsDimension,
    GoogleAdsMetricDaily,
)
from app.integrations.google_ads.reporting import Window
from app.integrations.google_ads.service import GoogleAdsService

logger = logging.getLogger("schakl.googleads")

#: How many trailing days the nightly run re-pulls. Ads attribution keeps moving for several
#: days after the click, so a week of overlap is what lets a late conversion self-heal into the
#: day it belongs to rather than never appearing at all.
TRAILING_DAYS = 7

#: What a first sync reaches back for: thirteen months, so a year-over-year comparison works the
#: day after an account is linked rather than a year after.
BACKFILL_DAYS = 400

#: One chunk per request. Ads answers a 400-day daily read happily enough, but a campaign-level
#: one at that span is tens of thousands of rows, and committing per chunk means an interrupted
#: backfill keeps the progress it made.
CHUNK_DAYS = 30


async def sync_account(
    session: AsyncSession,
    org: Org,
    account: GoogleAdsAccount,
    *,
    days: int = TRAILING_DAYS,
    ends_days_ago: int = 0,
) -> bool:
    """Mirror one account's window. Returns whether it succeeded.

    Never raises. A failure is recorded on the account and reported by the return value, because
    the caller is a loop over every account in the org and an exception would end it.

    ``ends_days_ago`` walks the window backwards for the backfill, so its chunks tile the
    **account's** calendar rather than the server's — the same reason the end is resolved from
    the account's timezone in the first place.
    """
    today = datetime.now(resolve_zoneinfo(account.time_zone)).date()
    end = today - timedelta(days=1 + ends_days_ago)
    start = end - timedelta(days=days - 1)
    window = Window(start=start, end=end)
    service = GoogleAdsService(SyncContext(org=org, session=session))
    try:
        async with service.open_client(account_id=account.id, tool="sync") as (client, _a):
            daily = await reporting.read_account_daily(client, account.customer_id, window)
            campaigns = await reporting.read_campaign_daily(client, account.customer_id, window)
            devices = await reporting.read_device_daily(client, account.customer_id, window)
            changes = await reporting.read_changes(
                client, account.customer_id, window, limit=reporting.LIMITS["changes"]
            )
    except AdsNotConfigured as exc:
        # A presentable state, not a failure to alarm anyone about: the account is unlinked from
        # its Google connection, or the org has no developer token yet.
        account.last_sync_error = str(exc)[:500]
        return False
    except AdsError as exc:
        account.last_sync_error = describe_failure(exc)
        logger.warning("google ads sync failed for %s: %s", account.customer_id, exc)
        return False
    except AppError as exc:
        # `AdsError` **is** an `AppError`, which is what lets every route surface a Google
        # refusal correctly without catching anything — but it also means an ordinary
        # `AppError` reaches here, and one does: a rotated `SCHAKL_ENCRYPTION_KEY` makes the
        # stored developer token unreadable and the service answers 409. Uncaught, that single
        # org-wide condition would escape the loop and leave every *later* account unsynced with
        # nothing on any row to say why.
        account.last_sync_error = exc.message_key[:500]
        logger.warning(
            "google ads sync could not run for %s: %s", account.customer_id, exc.message_key
        )
        return False

    await _upsert_daily(session, account, GoogleAdsDimension.ACCOUNT, daily)
    await _upsert_daily(session, account, GoogleAdsDimension.CAMPAIGN, campaigns)
    await _upsert_daily(session, account, GoogleAdsDimension.DEVICE, devices)
    await _upsert_changes(session, account, changes.rows)
    account.last_synced_at = datetime.now(UTC)
    account.last_sync_error = None
    return True


class SyncContext:
    """The minimum a service needs when there is no request and no user.

    Not ``SystemContext``: this runs under ``run_per_org``, which has already bound the RLS GUC,
    and the service only asks three things of a context — a session, an org, and a ``release_db``
    that is a no-op in a worker (a job owns its session outright and has nothing to hand back).
    ``can`` is always true for the same reason it is on ``SystemContext``: there is no principal
    here to authorize, and what gates a job is the job.
    """

    is_system = True

    def __init__(self, org: Org, session: AsyncSession) -> None:
        self.org = org
        self.session = session
        self.user = None
        self.company_scope: frozenset | None = None
        self.is_portal = False

    def repo(self, model: type[Any]) -> Any:
        from app.core.tenancy import TenantScopedRepository

        return TenantScopedRepository(self.session, self.org.id, model)

    def can(self, permission: str, scope: str | None = None) -> bool:  # noqa: ARG002
        return True

    def require(self, permission: str, scope: str | None = None) -> None:
        """No-op: a cron is gated by being a cron."""

    def release_db(self):
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _noop():
            yield

        return _noop()


async def _upsert_daily(
    session: AsyncSession,
    account: GoogleAdsAccount,
    dimension: GoogleAdsDimension,
    rows: list[dict[str, Any]],
) -> None:
    """Write the window's rows, overwriting whatever a previous run stored for those days.

    ``ON CONFLICT DO UPDATE`` rather than delete-then-insert: the two are not the same under a
    failure. A delete that commits and an insert that does not leaves the client's chart with a
    hole where last week used to be, and nothing says so.
    """
    if not rows:
        return
    payload = [
        {
            "org_id": account.org_id,
            "account_id": account.id,
            "date": row["date"],
            "dimension": dimension.value,
            "dim_key": row.get("dim_key") or "",
            "label": (row.get("label") or "")[:255],
            "metrics": row["metrics"],
            "currency": account.currency_code,
        }
        for row in rows
    ]
    statement = pg_insert(GoogleAdsMetricDaily).values(payload)
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_google_ads_metrics_daily_row",
            set_={
                "label": statement.excluded.label,
                "metrics": statement.excluded.metrics,
                "currency": statement.excluded.currency,
                "updated_at": datetime.now(UTC),
            },
        )
    )


async def _upsert_changes(
    session: AsyncSession, account: GoogleAdsAccount, rows: list[dict[str, Any]]
) -> None:
    """Mirror the change events, keyed on what a change *is* since Google gives no id."""
    zone = resolve_zoneinfo(account.time_zone)
    payload = []
    for row in rows:
        moment = _instant(row.get("changed_at"), zone)
        if moment is None or not row.get("changed_resource"):
            # Without both, the row cannot be identified — and an unidentifiable row would be
            # re-inserted every single night.
            continue
        payload.append(
            {
                "org_id": account.org_id,
                "account_id": account.id,
                "changed_at": moment,
                "resource_type": (row.get("resource_type") or "")[:64],
                "operation": (row.get("operation") or "")[:16],
                "changed_resource": (row.get("changed_resource") or "")[:512],
                "campaign": row.get("campaign"),
                "ad_group": row.get("ad_group"),
                "changed_by": (row.get("changed_by") or None),
                "client_type": (row.get("client_type") or None),
                "changed_fields": row.get("changed_fields") or [],
            }
        )
    if not payload:
        return
    statement = pg_insert(GoogleAdsChange).values(payload)
    await session.execute(
        statement.on_conflict_do_update(
            constraint="uq_google_ads_changes_event",
            # The *fields* can arrive fuller on a later read (Google backfills its own history),
            # so a re-mirror updates them rather than skipping.
            set_={"changed_fields": statement.excluded.changed_fields},
        )
    )


def _instant(raw: Any, zone) -> datetime | None:
    """Google's ``"2026-08-01 09:00:00"`` in the account's zone → an aware instant.

    Naive on the wire and local to the account, so attaching the zone is what makes two
    accounts in two countries sortable against each other at all.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace(" ", "T"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)


async def accounts_to_sync(session: AsyncSession, org: Org) -> list[GoogleAdsAccount]:
    return list(
        (
            await session.scalars(
                select(GoogleAdsAccount).where(
                    GoogleAdsAccount.org_id == org.id,
                    GoogleAdsAccount.active.is_(True),
                    GoogleAdsAccount.is_manager.is_(False),
                )
            )
        ).all()
    )
