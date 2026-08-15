"""Background work for ``snelstart`` (epic #377). Business-licensed — see LICENSE.

Two crons, and the split between them is about what each one is allowed to do while nobody is
watching.

**The nightly sync only reads.** It refreshes the vocabulary and folds SnelStart's outstanding
balances back into schakl, which are both answers to *"what happened over there?"*. Nothing in
anybody's ledger changes because a cron woke up.

**Pushing is opt-in per account** (``auto_push_invoices``, off by default). #31 says do not
auto-finalise financial documents, and an agency connecting an existing administration wants to
watch the first few land before trusting a scheduler with its books. Once they have, the cron
pushes what has been issued since — and reports what it could not, which is the half that makes
an automatic push safe to leave on.

Failures **notify**. A finance sync that fails quietly is one that is discovered when an
accountant asks why last month is missing, which is six weeks too late.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.jobs import run_per_org, system_context
from app.core.models import Org
from app.integrations.snelstart.models import (
    SnelstartAccount,
    SnelstartAccountStatus,
    SnelstartSyncRun,
)

logger = logging.getLogger("schakl.snelstart")


async def snelstart_nightly() -> None:
    """Refresh the vocabulary and reconcile payments, per org."""
    await run_per_org(_nightly_for_org)


async def _nightly_for_org(org: Org, session: AsyncSession) -> None:
    from app.integrations.snelstart.service import SnelstartAccountService
    from app.integrations.snelstart.sync import SnelstartSyncService

    ctx = system_context(org, session)
    accounts = list(
        (
            await session.execute(
                select(SnelstartAccount).where(
                    SnelstartAccount.org_id == org.id,
                    SnelstartAccount.active.is_(True),
                    SnelstartAccount.client_key_encrypted.is_not(None),
                )
            )
        ).scalars()
    )
    if not accounts:
        return

    for account in accounts:
        service = SnelstartAccountService(ctx)
        sync = SnelstartSyncService(ctx)
        runs: list[SnelstartSyncRun] = []
        try:
            runs.append(await service.sync_reference(account.id))
            runs.append(await sync.link_relations(account.id))
            if account.pull_payments:
                runs.append(await sync.reconcile_payments(account.id))
            if account.auto_push_invoices:
                runs.append(await sync.push_invoices(account.id))
        except Exception:  # noqa: BLE001 — one account's failure is not another's
            logger.exception(
                "snelstart: nightly sync failed for org %s account %s", org.slug, account.id
            )
            continue
        await _notify_failures(ctx, account, runs)


async def _notify_failures(ctx, account: SnelstartAccount, runs: list[SnelstartSyncRun]) -> None:
    """Tell somebody when an unattended finance sync did not do what it set out to.

    One notification per account per night rather than one per failed row: an administration
    whose credential expired would otherwise send four hundred, and the first one already said
    everything. ``_dedup_key`` carries the account so two connected administrations still each
    get a voice.

    Imported inside the function (§6): ``notifications`` is another module, and a hard import at
    module scope would make this one depend on it being enabled.
    """
    failed = [run for run in runs if not run.ok]
    if not failed:
        return
    from app.modules.notifications.service import NotificationService

    worst = failed[0]
    try:
        await NotificationService(ctx).ingest(
            "snelstart.sync.failed",
            "snelstart_account",
            account.id,
            {
                "account_name": account.name,
                "administration": account.administration_name or "",
                "kind": worst.kind,
                "message": worst.message or "",
                "failed": sum(int(run.counts.get("failed") or 0) for run in failed),
                "_dedup_key": f"snelstart.sync.failed:{account.id}",
            },
        )
    except Exception:  # noqa: BLE001 — a notification failure must not lose the sync's work
        logger.warning("snelstart: could not notify sync failure for account %s", account.id)


async def snelstart_prune_runs() -> None:
    """Keep the run log readable.

    A sync log is only useful while somebody can find last Tuesday's failure in it; a year of
    nightly runs across four kinds is four thousand rows nobody scrolls. Kept: the last 200 per
    account, which spans months of nightly runs and every manual one in between.
    """
    await run_per_org(_prune_for_org)


async def _prune_for_org(org: Org, session: AsyncSession) -> None:
    from sqlalchemy import delete

    accounts = (
        await session.execute(
            select(SnelstartAccount.id).where(SnelstartAccount.org_id == org.id)
        )
    ).scalars()
    for account_id in accounts:
        keep = (
            select(SnelstartSyncRun.id)
            .where(
                SnelstartSyncRun.org_id == org.id,
                SnelstartSyncRun.account_id == account_id,
            )
            .order_by(SnelstartSyncRun.created_at.desc())
            .limit(200)
            .scalar_subquery()
        )
        await session.execute(
            delete(SnelstartSyncRun).where(
                SnelstartSyncRun.org_id == org.id,
                SnelstartSyncRun.account_id == account_id,
                SnelstartSyncRun.id.not_in(keep),
            )
        )


def account_status_is_broken(account: SnelstartAccount) -> bool:
    """Whether this account should read as broken on a dashboard."""
    return account.status == SnelstartAccountStatus.ERROR.value
