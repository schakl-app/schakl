"""REST endpoints for automation under ``/api/v1/automation`` (CLAUDE.md §6, §9).

Route order: literal paths (``/catalog``, ``/runs``, ``/dry-run``) before ``/rules/{id}``
never collide here because the dynamic segment lives under ``/rules``; kept grouped anyway.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.automation.actions import available_actions
from app.modules.automation.schemas import (
    CatalogRead,
    DryRunRequest,
    DryRunResult,
    RuleCreate,
    RuleRead,
    RuleUpdate,
    RunRead,
    TriggerInfo,
)
from app.modules.automation.service import AutomationService
from app.modules.automation.triggers import TRIGGERS
from app.schemas import Page

router = APIRouter(prefix="/automation", tags=["automation"])


# --- catalog ---------------------------------------------------------------------------- #
@router.get(
    "/catalog",
    response_model=CatalogRead,
    dependencies=[require_permission("automation.rule.read")],
)
async def get_catalog(ctx: RequestContext = Depends(require_context)) -> CatalogRead:
    """The trigger events and action types the rule editor may offer (code-defined)."""
    return CatalogRead(
        triggers=[
            TriggerInfo(event=spec.event, entity_type=spec.entity_type)
            for spec in TRIGGERS.values()
        ],
        actions=[
            spec.key
            for spec in sorted(
                available_actions().values(), key=lambda spec: (spec.position, spec.key)
            )
        ],
    )


# --- rules ------------------------------------------------------------------------------ #
@router.get(
    "/rules",
    response_model=list[RuleRead],
    dependencies=[require_permission("automation.rule.read")],
)
async def list_rules(ctx: RequestContext = Depends(require_context)) -> list[RuleRead]:
    return await AutomationService(ctx).list()


@router.post(
    "/rules",
    response_model=RuleRead,
    status_code=201,
    dependencies=[require_permission("automation.rule.write")],
)
async def create_rule(
    payload: RuleCreate, ctx: RequestContext = Depends(require_context)
) -> RuleRead:
    return await AutomationService(ctx).create(payload)


@router.get(
    "/rules/{rule_id}",
    response_model=RuleRead,
    dependencies=[require_permission("automation.rule.read")],
)
async def get_rule(
    rule_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> RuleRead:
    return await AutomationService(ctx).get(rule_id)


@router.patch(
    "/rules/{rule_id}",
    response_model=RuleRead,
    dependencies=[require_permission("automation.rule.write")],
)
async def update_rule(
    rule_id: uuid.UUID, payload: RuleUpdate, ctx: RequestContext = Depends(require_context)
) -> RuleRead:
    return await AutomationService(ctx).update(rule_id, payload)


@router.delete(
    "/rules/{rule_id}",
    status_code=204,
    dependencies=[require_permission("automation.rule.write")],
)
async def delete_rule(
    rule_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    await AutomationService(ctx).delete(rule_id)


# --- dry-run ---------------------------------------------------------------------------- #
@router.post(
    "/dry-run",
    response_model=DryRunResult,
    dependencies=[require_permission("automation.rule.write")],
)
async def dry_run(
    payload: DryRunRequest, ctx: RequestContext = Depends(require_context)
) -> DryRunResult:
    """Evaluate a draft rule against a sample entity; executes nothing."""
    return await AutomationService(ctx).dry_run(payload)


# --- runs ------------------------------------------------------------------------------- #
@router.get(
    "/runs",
    response_model=Page[RunRead],
    dependencies=[require_permission("automation.run.read")],
)
async def list_runs(
    rule_id: uuid.UUID | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ctx: RequestContext = Depends(require_context),
) -> Page[RunRead]:
    items, total = await AutomationService(ctx).list_runs(
        rule_id=rule_id, limit=limit, offset=offset
    )
    return Page(items=items, total=total, limit=limit, offset=offset)
