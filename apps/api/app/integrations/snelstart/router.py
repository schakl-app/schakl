"""REST endpoints for snelstart under ``/api/v1/snelstart`` (epic #377, §6, §9, §15).

Business-licensed — see LICENSE.

Two things about the shape are deliberate.

**The read and the act are different routes.** ``GET /accounts`` answers from stored rows and
never calls SnelStart, so a settings screen loads at full speed and still renders when SnelStart
is down; ``POST /accounts/{id}/verify`` and the ``/sync/*`` routes are the explicit *go and look*
actions. ``oxxa``'s split, and it matters more here because the alternative — probing on page
load — makes an outage look like a broken integration.

**The coupling callback is the one unauthenticated route**, and every property it has is
load-bearing. See :func:`coupling_callback`.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Body, Depends, Query, Request, Response

from app.core.entitlements.service import license_exempt
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.snelstart.coupling import handle_coupling_callback
from app.integrations.snelstart.schemas import (
    SnelstartAccountCreate,
    SnelstartAccountRead,
    SnelstartAccountUpdate,
    SnelstartLedgerOption,
    SnelstartLinkAdopt,
    SnelstartLinkRead,
    SnelstartPushResult,
    SnelstartRelationCandidate,
    SnelstartSyncRunRead,
    SnelstartVerifyResult,
)
from app.integrations.snelstart.service import SnelstartAccountService
from app.integrations.snelstart.sync import SnelstartSyncService

router = APIRouter(prefix="/snelstart", tags=["snelstart"])

_MANAGE = "snelstart.settings.manage"
_SYNC = "snelstart.sync.run"
_WRITE = "snelstart.ledger.write"


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
@router.get(
    "/accounts",
    response_model=list[SnelstartAccountRead],
    dependencies=[require_permission(_MANAGE)],
)
async def list_accounts(
    ctx: RequestContext = Depends(require_context),
) -> list[SnelstartAccountRead]:
    """Connected administrations. The koppelsleutel is never part of the response."""
    return [
        SnelstartAccountRead(**row) for row in await SnelstartAccountService(ctx).list_accounts()
    ]


@router.get(
    "/accounts/options",
    response_model=list[SnelstartAccountRead],
    dependencies=[require_permission(_SYNC)],
)
async def account_options(
    ctx: RequestContext = Depends(require_context),
) -> list[SnelstartAccountRead]:
    """The accounts a sync or push may run against.

    Gated on the *weaker* ``sync.run`` rather than ``settings.manage``: choosing which
    administration to push into is the sync caller's job, and should not require holding the
    credential screen's permission (``oxxa``'s ``/accounts/options`` reasoning).
    """
    rows = await SnelstartAccountService(ctx).list_accounts()
    return [SnelstartAccountRead(**row) for row in rows if row["active"] and row["connected"]]


@router.post(
    "/accounts",
    response_model=SnelstartAccountRead,
    status_code=201,
    dependencies=[require_permission(_MANAGE)],
)
async def create_account(
    payload: SnelstartAccountCreate, ctx: RequestContext = Depends(require_context)
) -> SnelstartAccountRead:
    """Store a credential, or open a pending one for the activation flow to fill.

    Creating does not verify: ``/verify`` is the explicit probe, so a typo is reported on the
    settings screen beside the row rather than as a failed save that loses what was typed.
    """
    service = SnelstartAccountService(ctx)
    account = await service.create_account(payload)
    return SnelstartAccountRead(**service.serialize(account))


@router.patch(
    "/accounts/{account_id}",
    response_model=SnelstartAccountRead,
    dependencies=[require_permission(_MANAGE)],
)
async def update_account(
    account_id: uuid.UUID,
    payload: SnelstartAccountUpdate,
    ctx: RequestContext = Depends(require_context),
) -> SnelstartAccountRead:
    """Rename, rotate, or change what this connection does. An omitted key keeps the stored one."""
    service = SnelstartAccountService(ctx)
    account = await service.update_account(account_id, payload)
    return SnelstartAccountRead(**service.serialize(account))


@router.post(
    "/accounts/{account_id}/verify",
    response_model=SnelstartVerifyResult,
    dependencies=[require_permission(_MANAGE)],
)
async def verify_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SnelstartVerifyResult:
    """Ask SnelStart which administration this key opens, and what it may do there.

    Answers ``200`` with ``ok=false`` for a rejected credential rather than an error status: the
    probe succeeded, its answer was no, and the row keeps SnelStart's own words on it. The
    result also names *which* credential was refused — the tenant's koppelsleutel or the
    install's subscription key — because only one of those is something the agency can fix.
    """
    return await SnelstartAccountService(ctx).verify(account_id)


@router.delete(
    "/accounts/{account_id}", status_code=204, dependencies=[require_permission(_MANAGE)]
)
async def delete_account(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await SnelstartAccountService(ctx).delete_account(account_id)


# --------------------------------------------------------------------------- #
# Reference data
# --------------------------------------------------------------------------- #
@router.post(
    "/accounts/{account_id}/sync/reference",
    response_model=SnelstartSyncRunRead,
    dependencies=[require_permission(_SYNC)],
)
async def sync_reference(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SnelstartSyncRunRead:
    """Pull the administration's chart of accounts, journals, countries and article groups."""
    return SnelstartSyncRunRead.model_validate(
        await SnelstartAccountService(ctx).sync_reference(account_id)
    )


@router.get(
    "/accounts/{account_id}/ledgers",
    response_model=list[SnelstartLedgerOption],
    dependencies=[require_permission(_SYNC)],
)
async def ledger_options(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> list[SnelstartLedgerOption]:
    """The revenue accounts a line may book to — the picker behind the per-rate mapping.

    From the cache, never live: a settings screen that waits on SnelStart to draw a dropdown is
    a settings screen that hangs when SnelStart does.
    """
    return [
        SnelstartLedgerOption(**row)
        for row in await SnelstartAccountService(ctx).ledger_options(account_id)
    ]


@router.get(
    "/accounts/{account_id}/runs",
    response_model=list[SnelstartSyncRunRead],
    dependencies=[require_permission(_SYNC)],
)
async def list_runs(
    account_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    ctx: RequestContext = Depends(require_context),
) -> list[SnelstartSyncRunRead]:
    """What the last syncs did, and what they could not do (#31: failures are visible)."""
    return [
        SnelstartSyncRunRead.model_validate(row)
        for row in await SnelstartAccountService(ctx).recent_runs(account_id, limit)
    ]


# --------------------------------------------------------------------------- #
# Relations
# --------------------------------------------------------------------------- #
@router.get(
    "/accounts/{account_id}/relations",
    response_model=list[SnelstartRelationCandidate],
    dependencies=[require_permission(_SYNC)],
)
async def relation_candidates(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> list[SnelstartRelationCandidate]:
    """Every SnelStart customer and what schakl believes it is. Writes nothing.

    The review screen for a first connect, and the reason it exists rather than a silent merge:
    200 relations against 180 companies is an overlap nobody can eyeball, and each proposal says
    *why* it was made so an admin only has to actually read the guesses.
    """
    return await SnelstartSyncService(ctx).relation_candidates(account_id)


@router.post(
    "/accounts/{account_id}/sync/relations",
    response_model=SnelstartSyncRunRead,
    dependencies=[require_permission(_SYNC)],
)
async def link_relations(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SnelstartSyncRunRead:
    """Adopt every SnelStart customer schakl can identify without guessing.

    ``sync.run``, not ``ledger.write``: nothing outside schakl changes. It pairs records and
    records what it could not pair.
    """
    return SnelstartSyncRunRead.model_validate(
        await SnelstartSyncService(ctx).link_relations(account_id)
    )


@router.post(
    "/accounts/{account_id}/links/{link_id}/adopt",
    response_model=SnelstartLinkRead,
    dependencies=[require_permission(_SYNC)],
)
async def adopt_link(
    account_id: uuid.UUID,
    link_id: uuid.UUID,
    payload: SnelstartLinkAdopt,
    ctx: RequestContext = Depends(require_context),
) -> SnelstartLinkRead:
    """Pair a SnelStart row with a schakl record by hand — the reviewer's one click."""
    return SnelstartLinkRead.model_validate(
        await SnelstartSyncService(ctx).adopt_link(account_id, link_id, payload.local_id)
    )


@router.post(
    "/accounts/{account_id}/push/relations",
    response_model=SnelstartSyncRunRead,
    dependencies=[require_permission(_WRITE)],
)
async def push_relations(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SnelstartSyncRunRead:
    """Push paired and invoiced companies into SnelStart's relation file."""
    return SnelstartSyncRunRead.model_validate(
        await SnelstartSyncService(ctx).push_relations(account_id)
    )


# --------------------------------------------------------------------------- #
# Invoices
# --------------------------------------------------------------------------- #
@router.post(
    "/accounts/{account_id}/push/invoices",
    response_model=SnelstartSyncRunRead,
    dependencies=[
        require_permission(_WRITE),
        # Pushing an invoice into an accountant's ledger is an act on **an invoice**, and that
        # act is already gated. Declaring only this module's key would let a role that may not
        # touch invoices send them to the books; declaring only invoicing's would let anyone who
        # edits an invoice write to somebody else's accounts. Both, or neither is enough.
        require_permission("invoicing.invoice.write"),
    ],
)
async def push_invoices(
    account_id: uuid.UUID,
    invoice_ids: list[uuid.UUID] | None = Body(default=None, embed=True),
    ctx: RequestContext = Depends(require_context),
) -> SnelstartSyncRunRead:
    """Every issued invoice not yet in SnelStart, or a named selection.

    Idempotent by construction: a stored link, then a lookup by number, then SnelStart's own
    ``BOE-0021`` duplicate refusal, then — for a write that got no answer at all — a lookup
    before any retry. A duplicate invoice in a client's ledger is a real-world incident (#31),
    so the guard is four-deep rather than careful.
    """
    return SnelstartSyncRunRead.model_validate(
        await SnelstartSyncService(ctx).push_invoices(account_id, invoice_ids=invoice_ids)
    )


@router.post(
    "/accounts/{account_id}/push/invoices/{invoice_id}",
    response_model=SnelstartPushResult,
    dependencies=[
        require_permission(_WRITE),
        require_permission("invoicing.invoice.write"),
    ],
)
async def push_invoice(
    account_id: uuid.UUID,
    invoice_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> SnelstartPushResult:
    """One invoice, from its own detail page."""
    from app.modules.invoicing.service import InvoiceService

    service = SnelstartSyncService(ctx)
    account = await service.accounts.accounts.get_or_404(account_id)
    invoice = await InvoiceService(ctx).get(invoice_id)
    return await service.push_invoice(account, invoice)


@router.post(
    "/accounts/{account_id}/sync/payments",
    response_model=SnelstartSyncRunRead,
    dependencies=[require_permission(_SYNC)],
)
async def sync_payments(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SnelstartSyncRunRead:
    """Fold SnelStart's outstanding balances back into schakl as payments.

    ``sync.run``, not ``ledger.write``, and the distinction is exact: this writes into *schakl*
    and changes nothing in the administration. It is also the answer to "who hasn't paid", which
    is the reason most agencies want this integration at all.
    """
    return SnelstartSyncRunRead.model_validate(
        await SnelstartSyncService(ctx).reconcile_payments(account_id)
    )


# --------------------------------------------------------------------------- #
# Articles
# --------------------------------------------------------------------------- #
@router.post(
    "/accounts/{account_id}/push/articles",
    response_model=SnelstartSyncRunRead,
    dependencies=[require_permission(_WRITE)],
)
async def push_articles(
    account_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> SnelstartSyncRunRead:
    """schakl's products into SnelStart's article file, matched on the article code."""
    return SnelstartSyncRunRead.model_validate(
        await SnelstartSyncService(ctx).push_articles(account_id)
    )


# --------------------------------------------------------------------------- #
# The coupling callback — the one unauthenticated route
# --------------------------------------------------------------------------- #
@router.post(
    "/coupling/callback",
    dependencies=[
        no_permission_required(
            "SnelStart posts a granted koppelsleutel here with no session and no tenant "
            "hostname — it posts to the one URL registered for the whole partner app. The "
            "referenceKey we minted names the tenant and its secret authenticates the call, "
            "exactly as the payment callback's token does (app.core.payments.tokens)."
        )
    ],
)
@license_exempt(
    "A koppelsleutel arrives once and SnelStart never retries. A 402 here would drop a "
    "credential a tenant has already approved, with no mechanism anywhere that would ever "
    "deliver it again — and the tenant would see a connect flow that silently did nothing."
)
async def coupling_callback(request: Request, response: Response) -> dict[str, str]:
    """Receive a koppelsleutel SnelStart has just granted.

    ``{KoppelSleutel, ActionType: "Create"|"Regenerate"|"Delete", ReferenceKey}``. Five gates, in
    this order and no other — the payment webhook's order, because the problem is the same one:

    1. **The reference names the tenant.** No hostname, no session, no unscoped lookup. On cloud
       this arrives on the instance apex, where no org resolves at all, so there is nothing else
       it could come from.
    2. **The RLS GUC is bound before anything is read**, which is what makes gate 3 safe to run
       against attacker-chosen ids.
    3. **The secret is compared in constant time**, and a mismatch is a bare 404 — never 401 or
       403, which would confirm that the account exists.
    4. **The body is a hint, never a fact.** It names a key; the key proves itself by minting a
       token and reading ``/companyInfo``. A payload that merely *claims* to be a credential is
       stored only after it has behaved like one — and that re-fetch is also what records which
       administration it opens.
    5. **``Delete`` disconnects, it does not delete the row.** The links, the mappings and the
       run history are the tenant's record of what happened; throwing them away because
       somebody revoked a key in SnelStart would destroy the audit trail of a ledger.

    Answers ``200`` for anything it cannot act on, deliberately: SnelStart treats 2xx as
    delivered and **does not retry**, so the only thing a non-2xx buys is a tenant staring at a
    connect flow that failed silently. What we cannot process is logged, not bounced.
    """
    body = await request.body()
    status = await handle_coupling_callback(body)
    response.status_code = status
    return {"status": "ok" if status == 200 else "ignored"}
