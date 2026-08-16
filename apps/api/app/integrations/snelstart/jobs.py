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
        await _notify_failures(ctx, session, account, runs)


async def _notify_failures(
    ctx, session: AsyncSession, account: SnelstartAccount, runs: list[SnelstartSyncRun]
) -> None:
    """Tell somebody when an unattended finance sync did not do what it set out to.

    **The recipients are named, and that is the whole point.** A notification's default audience
    is the *watchers* of its entity, and nobody watches a ``snelstart_account`` — there is no
    screen on which to start. Emitting without a hint would write an event row with an empty
    audience: a "failures are visible" requirement (#31) satisfied on paper and by nobody in
    practice. So it goes to the people who can actually act on it, which is the people holding
    ``snelstart.settings.manage``.

    One notification per account per night rather than one per failed row: an administration
    whose credential expired would otherwise send four hundred, and the first one already said
    everything. The dedup key carries the account *and the day*, so two connected administrations
    each get a voice and tomorrow's failure is still news.

    Imported inside the function (§6): ``notifications`` is another module, and a hard import at
    module scope would make this one depend on it being enabled.
    """
    failed = [run for run in runs if not run.ok]
    if not failed:
        return
    recipients = await _managers(session, account.org_id)
    if not recipients:
        # Nobody may administer this connection, so there is nobody to tell. Silence here is
        # correct and worth being explicit about: the alternative is an event nobody can open.
        logger.info("snelstart: sync failed for account %s and nobody holds the key", account.id)
        return

    from app.modules.notifications.service import NotificationService

    worst = failed[0]
    day = worst.created_at.date().isoformat() if worst.created_at else ""
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
                "_recipients": recipients,
                "_dedup_key": f"snelstart.sync.failed:{account.id}:{day}",
            },
        )
    except Exception:  # noqa: BLE001 — a notification failure must not lose the sync's work
        logger.warning("snelstart: could not notify sync failure for account %s", account.id)


async def _managers(session: AsyncSession, org_id) -> list:
    """Everyone in this org who may administer a SnelStart connection.

    Read through the RBAC tables directly rather than through ``ctx.can``, which answers about
    *one* caller — here the question is the other way round, and it is asked once per night per
    account. The owner's ``"*"`` is matched explicitly: it is stored literally on
    ``role_permissions`` and would otherwise make an owner the one person never told.
    """
    from app.core.models import Membership
    from app.core.permissions.models import MembershipRole, RolePermission

    rows = await session.execute(
        select(Membership.user_id)
        .join(MembershipRole, MembershipRole.membership_id == Membership.id)
        .join(RolePermission, RolePermission.role_id == MembershipRole.role_id)
        .where(
            Membership.org_id == org_id,
            RolePermission.org_id == org_id,
            RolePermission.permission.in_(["*", "snelstart.settings.manage"]),
        )
        .distinct()
    )
    return [row[0] for row in rows]


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
