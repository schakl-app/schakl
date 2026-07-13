"""REST endpoints for interactions (contactmomenten) under ``/api/v1/interactions``."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.interactions.schemas import (
    InteractionCreate,
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
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> Page[InteractionRead]:
    items, total = await InteractionService(ctx).list(
        limit=limit,
        offset=offset,
        company_id=company_id,
        project_id=project_id,
        task_id=task_id,
        contact_id=contact_id,
        kind=kind,
        status=status,
        owner_user_id=ctx.user.id if mine else owner_user_id,
        include=include,
        q=q,
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
    ctx: RequestContext = Depends(require_context),
) -> InteractionRead:
    return InteractionRead.model_validate(await InteractionService(ctx).approve(interaction_id))


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
