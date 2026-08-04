"""REST endpoints for interactions (contactmomenten) under ``/api/v1/interactions``."""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.interactions.schemas import (
    InteractionAddToConversation,
    InteractionApprove,
    InteractionBulkApprove,
    InteractionBulkAssign,
    InteractionBulkReject,
    InteractionBulkResult,
    InteractionCreate,
    InteractionEmlUploadRead,
    InteractionKindDefCreate,
    InteractionKindDefRead,
    InteractionKindDefUpdate,
    InteractionRead,
    InteractionReject,
    InteractionRemap,
    InteractionUpdate,
)
from app.modules.interactions.service import InteractionKindService, InteractionService
from app.schemas import Page

router = APIRouter(prefix="/interactions", tags=["interactions"])


# --- interaction kinds (#174) ------------------------------------------------ #
# Declared before ``/{interaction_id}`` so "kinds" never matches the id path param.
@router.get(
    "/kinds",
    response_model=list[InteractionKindDefRead],
    dependencies=[require_permission("interactions.kind.read")],
)
async def list_interaction_kinds(
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[InteractionKindDefRead]:
    items = await InteractionKindService(ctx).list(include_inactive=include_inactive)
    return [InteractionKindDefRead.model_validate(k) for k in items]


@router.post(
    "/kinds",
    response_model=InteractionKindDefRead,
    status_code=201,
    dependencies=[require_permission("interactions.kind.manage")],
)
async def create_interaction_kind(
    payload: InteractionKindDefCreate,
    ctx: RequestContext = Depends(require_context),
) -> InteractionKindDefRead:
    return InteractionKindDefRead.model_validate(await InteractionKindService(ctx).create(payload))


@router.patch(
    "/kinds/{kind_id}",
    response_model=InteractionKindDefRead,
    dependencies=[require_permission("interactions.kind.manage")],
)
async def update_interaction_kind(
    kind_id: uuid.UUID,
    payload: InteractionKindDefUpdate,
    ctx: RequestContext = Depends(require_context),
) -> InteractionKindDefRead:
    return InteractionKindDefRead.model_validate(
        await InteractionKindService(ctx).update(kind_id, payload)
    )


@router.delete(
    "/kinds/{kind_id}",
    status_code=204,
    dependencies=[require_permission("interactions.kind.manage")],
)
async def delete_interaction_kind(
    kind_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await InteractionKindService(ctx).delete(kind_id)


@router.get(
    "",
    response_model=Page[InteractionRead],
    dependencies=[require_permission("interactions.interaction.read")],
)
async def list_interactions(
    company_id: uuid.UUID | None = Query(None),
    project_id: uuid.UUID | None = Query(None),
    task_id: uuid.UUID | None = Query(None),
    contact_id: uuid.UUID | None = Query(None),
    kind: str | None = Query(None, max_length=50),
    status: str | None = Query(None, max_length=10),
    owner_user_id: uuid.UUID | None = Query(None),
    mine: bool = Query(False, description="Only my own rows — the review queue's filter"),
    include: str | None = Query(
        None,
        max_length=30,
        description="Roll-up: 'tasks' with project_id also returns the project's tasks' rows",
    ),
    q: str | None = Query(None, max_length=200, description="Free text over subject/snippet/body"),
    date_from: date | None = Query(None, description="Occurred on/after this org-local day"),
    date_to: date | None = Query(None, description="Occurred on/before this org-local day"),
    sort: str | None = Query(
        None, description="occurred_at | subject | kind | contact | owner, '-' desc"
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    count: bool = Query(
        True,
        description="Compute the total. False skips a second full pass over the filter.",
    ),
    with_body: bool = Query(
        False,
        description="Include each row's full body_text. Off by default — the list draws snippet.",
    ),
    ctx: RequestContext = Depends(require_context),
) -> Page[InteractionRead]:
    items, total = await InteractionService(ctx).list(
        limit=limit,
        offset=offset,
        count=count,
        with_body=with_body,
        company_id=company_id,
        project_id=project_id,
        task_id=task_id,
        contact_id=contact_id,
        kind=kind,
        status=status,
        owner_user_id=ctx.user.id if mine else owner_user_id,
        include=include,
        q=q,
        date_from=date_from,
        date_to=date_to,
        sort=sort,
    )
    return Page(
        items=[InteractionRead.model_validate(i) for i in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=InteractionRead,
    status_code=201,
    dependencies=[require_permission("interactions.interaction.write")],
)
async def create_interaction(
    payload: InteractionCreate,
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    return InteractionRead.model_validate(await InteractionService(ctx).create(payload))


@router.post(
    "/upload-eml",
    response_model=InteractionEmlUploadRead,
    status_code=201,
    dependencies=[require_permission("interactions.interaction.write")],
)
async def upload_interaction_eml(
    file: UploadFile = File(..., description="An exported .eml message"),
    company_id: uuid.UUID | None = Form(None),
    project_id: uuid.UUID | None = Form(None),
    task_id: uuid.UUID | None = Form(None),
    contact_id: uuid.UUID | None = Form(None),
    contact_ids: list[uuid.UUID] | None = Form(
        None, description="Everyone the message was with; wins over contact_id"
    ),
    allow_duplicate: bool = Form(
        False, description="Log it even though this Message-ID is already on the timeline"
    ),
    ctx: RequestContext = Depends(require_context),
) -> InteractionEmlUploadRead:
    """Log an exported email as a contactmoment (#262).

    The narrow, audited path that may write the protected ``email`` kind: the ordinary
    ``POST /interactions`` still refuses it, because only a real message — parsed, not typed —
    may claim to be one. Links may be assigned in the same step, exactly like approving a
    gmail row (#183). Declared before ``/{interaction_id}`` so the literal path always wins.
    """
    # UploadFile spools to disk past a small threshold; size it without trusting the client.
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    data = await file.read() if size else b""
    interaction, stored, skipped = await InteractionService(ctx).create_from_eml(
        data=data,
        filename=file.filename or "",
        content_type=file.content_type,
        links={
            "company_id": company_id,
            "project_id": project_id,
            "task_id": task_id,
            "contact_id": contact_id,
            # Only when the caller actually sent it: an absent key is what lets the service's
            # one contact contract (schemas.py) fall back to ``contact_id`` for older callers.
            **({"contact_ids": contact_ids} if contact_ids is not None else {}),
        },
        allow_duplicate=allow_duplicate,
    )
    return InteractionEmlUploadRead(
        interaction=InteractionRead.model_validate(interaction),
        attachments_stored=stored,
        attachments_skipped=skipped,
    )


# --- bulk review (#299) ------------------------------------------------------------ #
# Declared before ``/{interaction_id}/…`` — otherwise ``/bulk/approve`` matches *that* route
# with ``interaction_id="bulk"`` and answers 422 instead of doing the thing.
#
# All three carry ``interactions.interaction.review``, the same permission the single-row
# endpoints do, and no new one. Bulk export earns its own capability (§17) because taking the
# client list out of the building in one file is a *different act* from opening one record;
# approving forty emails you may each approve is the same act, repeated. Inventing
# ``interaction.bulk_review`` would only add a switch that can be off while the thing it
# guards is still reachable one click at a time.
@router.post(
    "/bulk/approve",
    response_model=InteractionBulkResult,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def bulk_approve_interactions(
    payload: InteractionBulkApprove,
    ctx: RequestContext = Depends(require_context),
) -> InteractionBulkResult:
    """Approve a selection, optionally filing all of it in one step.

    Sending no link fields is "approve as matched": each row keeps the client/project the
    gmail feed derived for it. Rows are independent — an ineligible one comes back in
    ``failed`` rather than rolling the batch back.
    """
    return InteractionBulkResult.model_validate(await InteractionService(ctx).bulk_approve(payload))


@router.post(
    "/bulk/assign",
    response_model=InteractionBulkResult,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def bulk_assign_interactions(
    payload: InteractionBulkAssign,
    ctx: RequestContext = Depends(require_context),
) -> InteractionBulkResult:
    """File a selection without approving it — the batch form of remap, so it re-files logged
    rows too. An absent link field leaves every row's own alone."""
    return InteractionBulkResult.model_validate(await InteractionService(ctx).bulk_assign(payload))


@router.post(
    "/bulk/reject",
    response_model=InteractionBulkResult,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def bulk_reject_interactions(
    payload: InteractionBulkReject,
    ctx: RequestContext = Depends(require_context),
) -> InteractionBulkResult:
    """Reject a selection. Permanent per row: the metadata goes and the message is suppressed,
    so a re-poll never resurrects it."""
    return InteractionBulkResult.model_validate(await InteractionService(ctx).bulk_reject(payload))


@router.get(
    "/{interaction_id}",
    response_model=InteractionRead,
    dependencies=[require_permission("interactions.interaction.read")],
)
async def get_interaction(
    interaction_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    return InteractionRead.model_validate(await InteractionService(ctx).get(interaction_id))


@router.patch(
    "/{interaction_id}",
    response_model=InteractionRead,
    dependencies=[require_permission("interactions.interaction.write")],
)
async def update_interaction(
    interaction_id: uuid.UUID,
    payload: InteractionUpdate,
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    return InteractionRead.model_validate(
        await InteractionService(ctx).update(interaction_id, payload)
    )


@router.delete(
    "/{interaction_id}",
    status_code=204,
    dependencies=[require_permission("interactions.interaction.delete")],
)
async def delete_interaction(
    interaction_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await InteractionService(ctx).delete(interaction_id)


# --- gmail review flow: strictly the mailbox owner's call (service-enforced) -------- #
@router.post(
    "/{interaction_id}/approve",
    response_model=InteractionRead,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def approve_interaction(
    interaction_id: uuid.UUID,
    payload: InteractionApprove | None = None,
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    return InteractionRead.model_validate(
        await InteractionService(ctx).approve(interaction_id, payload)
    )


@router.post(
    "/{interaction_id}/reject",
    status_code=204,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def reject_interaction(
    interaction_id: uuid.UUID,
    payload: InteractionReject | None = None,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await InteractionService(ctx).reject(
        interaction_id, suppress_thread=bool(payload and payload.suppress_thread)
    )


@router.post(
    "/{interaction_id}/remap",
    response_model=InteractionRead,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def remap_interaction(
    interaction_id: uuid.UUID,
    payload: InteractionRemap,
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    return InteractionRead.model_validate(
        await InteractionService(ctx).remap(interaction_id, payload)
    )


@router.post(
    "/{interaction_id}/add-to-conversation",
    response_model=InteractionRead,
    dependencies=[require_permission("interactions.interaction.review")],
)
async def add_interaction_to_conversation(
    interaction_id: uuid.UUID,
    payload: InteractionAddToConversation,
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    """Manually glue this gmail email onto another's conversation (#272). Gated on ``.review``
    like every gmail-row mutation — the service enforces strict mailbox ownership on both the
    row and the target."""
    return InteractionRead.model_validate(
        await InteractionService(ctx).add_to_conversation(
            interaction_id, payload.target_interaction_id
        )
    )


@router.get(
    "/{interaction_id}/thread",
    response_model=list[InteractionRead],
    dependencies=[require_permission("interactions.interaction.read")],
)
async def get_interaction_thread(
    interaction_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[InteractionRead]:
    """The full conversation this interaction belongs to (#272), newest first — what the detail
    modal expands into. A row not in a conversation is its own one-message thread."""
    items = await InteractionService(ctx).thread(interaction_id)
    return [InteractionRead.model_validate(i) for i in items]
