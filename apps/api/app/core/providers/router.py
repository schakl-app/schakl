"""REST endpoints for the provider catalog under ``/api/v1/providers`` (issue #89, CLAUDE.md §9).

Reads are gated on ``settings.providers.read`` (all staff, so pickers work); writes on
``settings.providers.manage`` (the Instellingen admin surface). Deny-by-default (§15).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.providers.models import ProviderKind
from app.core.providers.schemas import ProviderCreate, ProviderRead, ProviderUpdate
from app.core.providers.service import ProviderService
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/providers", tags=["providers"])


@router.get(
    "",
    response_model=list[ProviderRead],
    dependencies=[require_permission("settings.providers.read")],
)
async def list_providers(
    kind: ProviderKind | None = Query(None),
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[ProviderRead]:
    items = await ProviderService(ctx).list(kind=kind, include_inactive=include_inactive)
    return [ProviderRead.model_validate(p) for p in items]


@router.post(
    "",
    response_model=ProviderRead,
    status_code=201,
    dependencies=[require_permission("settings.providers.manage")],
)
async def create_provider(
    payload: ProviderCreate,
    ctx: RequestContext = Depends(require_context),
) -> ProviderRead:
    provider = await ProviderService(ctx).create(payload)
    return ProviderRead.model_validate(provider)


@router.get(
    "/{provider_id}",
    response_model=ProviderRead,
    dependencies=[require_permission("settings.providers.read")],
)
async def get_provider(
    provider_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> ProviderRead:
    provider = await ProviderService(ctx).get(provider_id)
    return ProviderRead.model_validate(provider)


@router.patch(
    "/{provider_id}",
    response_model=ProviderRead,
    dependencies=[require_permission("settings.providers.manage")],
)
async def update_provider(
    provider_id: uuid.UUID,
    payload: ProviderUpdate,
    ctx: RequestContext = Depends(require_context),
) -> ProviderRead:
    provider = await ProviderService(ctx).update(provider_id, payload)
    return ProviderRead.model_validate(provider)


@router.delete(
    "/{provider_id}",
    status_code=204,
    dependencies=[require_permission("settings.providers.manage")],
)
async def delete_provider(
    provider_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await ProviderService(ctx).delete(provider_id)
