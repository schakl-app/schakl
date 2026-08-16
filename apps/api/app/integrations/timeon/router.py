"""REST endpoints for timeon under ``/api/v1/timeon`` (§6, §9, §15). Business-licensed — LICENSE.

Three things about the shape are deliberate.

**The read and the act are different routes.** Everything under ``GET`` answers from stored rows
and never calls Timeon, so the settings screen and the sync workspace load at full speed and
still render when Timeon is down. ``POST …/verify`` and ``POST …/sync`` are the explicit *go and
look* actions (``oxxa``'s split, and it matters more here: probing on page load makes an outage
look like a broken integration).

**A write and a dry run are the same route with different permissions.** ``POST …/sync`` declares
``timeon.sync.run``; the moment ``dry_run`` is false it also requires ``timeon.sync.write``
**and** ``time.entry.write:any``. The last of those is #314's rule — a ride-along write carries
the gates of the module it writes into, not of the route it rode in on — and it is the one that
stops this integration being a second, quieter way to rewrite an employee's timesheet.

**A run is returned, never awaited-and-forgotten.** The response *is* the report: what it read,
what it wrote, what it refused and what it needs a human for. A sync that answers ``{"ok": true}``
is a sync nobody can check.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.timeon.conflicts import TimeonConflictService
from app.integrations.timeon.models import TimeonLink
from app.integrations.timeon.presenters import (
    present_conflicts,
    present_links,
    present_runs,
    workspace_payload,
)
from app.integrations.timeon.schemas import (
    TimeonAccountCreate,
    TimeonAccountRead,
    TimeonAccountUpdate,
    TimeonConflictRead,
    TimeonConflictResolve,
    TimeonLinkRead,
    TimeonSyncRequest,
    TimeonSyncRunRead,
    TimeonVerifyResult,
    TimeonWorkspaceRead,
)
from app.integrations.timeon.service import TimeonAccountService
from app.integrations.timeon.sync import TimeonSyncService

router = APIRouter(prefix="/timeon", tags=["timeon"])

_MANAGE = "timeon.settings.manage"
_SYNC = "timeon.sync.run"
_WRITE = "timeon.sync.write"


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
@router.get(
    "/accounts",
    response_model=list[TimeonAccountRead],
    dependencies=[require_permission(_MANAGE)],
)
async def list_accounts(
    ctx: RequestContext = Depends(require_context),
) -> list[TimeonAccountRead]:
    """Connected Timeon organisations. The API key is never part of the response."""
    return [TimeonAccountRead(**row) for row in await TimeonAccountService(ctx).list_accounts()]


@router.get(
    "/accounts/options",
    response_model=list[TimeonAccountRead],
    dependencies=[require_permission(_SYNC)],
)
async def account_options(
    ctx: RequestContext = Depends(require_context),
) -> list[TimeonAccountRead]:
    """The connections a sync may run against.

    Gated on the weaker ``sync.run`` rather than ``settings.manage``: choosing which organisation
    to sync is the operator's job and should not require holding the credential screen's key.
    """
    rows = await TimeonAccountService(ctx).list_accounts()
    return [TimeonAccountRead(**row) for row in rows if row["active"] and row["connected"]]


@router.post(
    "/accounts",
    response_model=TimeonAccountRead,
    status_code=201,
    dependencies=[require_permission(_MANAGE)],
)
async def create_account(
    payload: TimeonAccountCreate, ctx: RequestContext = Depends(require_context)
) -> TimeonAccountRead:
    """Store a credential. Both directions start at ``off``: a connection that began syncing the
    moment a key was pasted would be an irreversible act performed by a form."""
    service = TimeonAccountService(ctx)
    return TimeonAccountRead(**service.serialize(await service.create_account(payload)))


@router.patch(
    "/accounts/{account_id}",
    response_model=TimeonAccountRead,
    dependencies=[require_permission(_MANAGE)],
)
async def update_account(
    account_id: uuid.UUID,
    payload: TimeonAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> TimeonAccountRead:
    """Rename, rotate the key, or change what the sync does. An omitted key keeps the stored one."""
    service = TimeonAccountService(ctx)
    return TimeonAccountRead(
        **service.serialize(await service.update_account(account_id, payload))
    )


@router.post(
    "/accounts/{account_id}/verify",
    response_model=TimeonVerifyResult,
    dependencies=[require_permission(_MANAGE)],
)
async def verify_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> TimeonVerifyResult:
    """Ask Timeon which organisation this key opens, and how big it is.

    Answers ``200`` with ``ok=false`` for a refused credential rather than an error status: the
    probe succeeded, its answer was no, and the row keeps Timeon's own words on it — raising here
    would roll back the very row that records what happened.
    """
    return await TimeonAccountService(ctx).verify(account_id)


@router.delete(
    "/accounts/{account_id}", status_code=204, dependencies=[require_permission(_MANAGE)]
)
async def delete_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    """Forget the connection. Pairings, conflicts and runs go with it; **time entries do not** —
    a pulled entry is schakl's record of work somebody did, and removing a credential is not a
    statement about whether that work happened."""
    await TimeonAccountService(ctx).delete_account(account_id)


# --------------------------------------------------------------------------- #
# Syncing
# --------------------------------------------------------------------------- #
@router.post(
    "/accounts/{account_id}/sync",
    response_model=TimeonSyncRunRead,
    dependencies=[require_permission(_SYNC)],
)
async def run_sync(
    account_id: uuid.UUID,
    payload: TimeonSyncRequest,
    ctx: RequestContext = Depends(require_context),
) -> TimeonSyncRunRead:
    """Run one sync and answer with its report.

    A dry run needs only ``timeon.sync.run`` — it is a read of both systems and a piece of
    arithmetic. A real one additionally needs ``timeon.sync.write`` *and* ``time.entry.write`` at
    ``:any``, because what it writes are other people's hours (#314, and §15's rule that the
    service refines what the route declares).
    """
    service = TimeonAccountService(ctx)
    account = await service.get_or_404(account_id)
    if not payload.dry_run:
        ctx.require(_WRITE)
        ctx.require("time.entry.write", scope="any")
    run = await TimeonSyncService(ctx, account).run(
        kind=payload.kind,
        dry_run=payload.dry_run,
        window_from=payload.window_from,
        window_to=payload.window_to,
        actor_user_id=None if ctx.is_system else ctx.user.id,
    )
    return (await present_runs(ctx, [run]))[0]


@router.get(
    "/runs",
    response_model=list[TimeonSyncRunRead],
    dependencies=[require_permission(_SYNC)],
)
async def list_runs(
    account_id: uuid.UUID | None = None,
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> list[TimeonSyncRunRead]:
    """What the last runs did, and what they could not do."""
    rows, _total = await TimeonAccountService(ctx).list_runs(
        account_id, limit=limit, offset=offset
    )
    return await present_runs(ctx, rows)


@router.get(
    "/links",
    response_model=list[TimeonLinkRead],
    dependencies=[require_permission(_SYNC)],
)
async def list_links(
    account_id: uuid.UUID | None = None,
    kind: str | None = None,
    status: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> list[TimeonLinkRead]:
    """The pairings, filterable by what is wrong with them.

    The company horizon applies by construction: ``timeon_links.company_id`` is a real column
    with a real FK precisely so a restricted staff member's read filters (#285 failure mode 1 —
    a link with no anchor would have filtered nothing at all).
    """
    return await present_links(
        ctx, account_id=account_id, kind=kind, status=status, limit=limit, offset=offset
    )


@router.delete("/links/{link_id}", status_code=204, dependencies=[require_permission(_WRITE)])
async def unpair(link_id: uuid.UUID, ctx: RequestContext = Depends(require_context)) -> None:
    """Forget one pairing without touching either record.

    Its own route because "these two are not the same thing" is a different act from every
    resolution in the conflict queue, and the alternative — editing rows by hand — is not an
    act an agency should have to ask for. The next run treats both sides as new.
    """
    repo = ctx.repo(TimeonLink)
    await repo.delete(await repo.get_or_404(link_id))


# --------------------------------------------------------------------------- #
# Conflicts
# --------------------------------------------------------------------------- #
@router.get(
    "/conflicts",
    response_model=list[TimeonConflictRead],
    dependencies=[require_permission(_SYNC)],
)
async def list_conflicts(
    account_id: uuid.UUID | None = None,
    status: str | None = Query("open"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> list[TimeonConflictRead]:
    """The queue. Readable on ``sync.run`` — seeing what needs deciding must not require the
    power to decide it."""
    return await present_conflicts(
        ctx, account_id=account_id, status=status, limit=limit, offset=offset
    )


@router.post(
    "/conflicts/{conflict_id}/resolve",
    response_model=TimeonConflictRead,
    dependencies=[require_permission(_WRITE)],
)
async def resolve_conflict(
    conflict_id: uuid.UUID,
    payload: TimeonConflictResolve,
    ctx: RequestContext = Depends(require_context),
) -> TimeonConflictRead:
    """Settle one conflict: keep schakl's version, keep Timeon's, or record that they may differ.

    Writing into schakl needs ``time.entry.write:any`` for the same reason the sync does, and it
    is asked for here rather than left to the engine so a caller holding only ``timeon.sync.write``
    cannot reach an employee's hours through the queue.
    """
    ctx.require("time.entry.write", scope="any")
    conflict = await TimeonConflictService(ctx).resolve(
        conflict_id, payload.resolution, payload.note
    )
    rows = await present_conflicts(ctx, conflict_ids=[conflict.id], status=None, limit=1, offset=0)
    return rows[0]


# --------------------------------------------------------------------------- #
# The workspace
# --------------------------------------------------------------------------- #
@router.get(
    "/workspace",
    response_model=TimeonWorkspaceRead,
    dependencies=[require_permission(_SYNC)],
)
async def workspace(
    account_id: uuid.UUID | None = None,
    ctx: RequestContext = Depends(require_context),
) -> TimeonWorkspaceRead:
    """Everything the sync page's shell draws, in one round trip.

    One endpoint rather than five, for the reason the Tag Manager container page was rebuilt
    (docs/GOOGLE_TAG_MANAGER.md §3a): four reads that each resolve the same account are four
    round trips for one screen. Nothing here calls Timeon, so it is fast and it renders during
    an outage — which is exactly when somebody opens it.
    """
    return await workspace_payload(ctx, account_id)
