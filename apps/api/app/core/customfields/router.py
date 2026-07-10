"""Custom-fields definitions API (CLAUDE.md §13).

``/api/v1/custom-fields/definitions`` — any member may read the schema for an entity type (so
clients can render fields, labels, order, and validation); only managers (owner/admin) may CRUD
definitions. ``/entity-types`` lists which entity types accept custom fields (registry-driven),
so the admin UI needs no per-module code.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.customfields.registry import customizable_entity_types
from app.core.customfields.schemas import (
    CustomFieldDefinitionCreate,
    CustomFieldDefinitionRead,
    CustomFieldDefinitionUpdate,
)
from app.core.customfields.service import CustomFieldsService
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/custom-fields", tags=["custom-fields"])


@router.get(
    "/entity-types",
    response_model=list[str],
    dependencies=[require_permission("settings.customfields.read")],
)
async def list_entity_types(
    _: RequestContext = Depends(require_context),
) -> list[str]:
    return customizable_entity_types()


@router.get(
    "/definitions",
    response_model=list[CustomFieldDefinitionRead],
    dependencies=[require_permission("settings.customfields.read")],
)
async def list_definitions(
    entity_type: str = Query(..., min_length=1),
    include_inactive: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> list[CustomFieldDefinitionRead]:
    defs = await CustomFieldsService(ctx).definitions(
        entity_type, include_inactive=include_inactive
    )
    return [CustomFieldDefinitionRead.model_validate(d) for d in defs]


@router.post(
    "/definitions",
    response_model=CustomFieldDefinitionRead,
    status_code=201,
    dependencies=[require_permission("settings.customfields.write")],
)
async def create_definition(
    payload: CustomFieldDefinitionCreate,
    ctx: RequestContext = Depends(require_context),
) -> CustomFieldDefinitionRead:
    definition = await CustomFieldsService(ctx).create_definition(payload)
    return CustomFieldDefinitionRead.model_validate(definition)


@router.patch(
    "/definitions/{definition_id}",
    response_model=CustomFieldDefinitionRead,
    dependencies=[require_permission("settings.customfields.write")],
)
async def update_definition(
    definition_id: uuid.UUID,
    payload: CustomFieldDefinitionUpdate,
    ctx: RequestContext = Depends(require_context),
) -> CustomFieldDefinitionRead:
    definition = await CustomFieldsService(ctx).update_definition(definition_id, payload)
    return CustomFieldDefinitionRead.model_validate(definition)


@router.delete(
    "/definitions/{definition_id}",
    status_code=204,
    dependencies=[require_permission("settings.customfields.write")],
)
async def delete_definition(
    definition_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    await CustomFieldsService(ctx).delete_definition(definition_id)
