"""Activity feed API (issue #67).

``/api/v1/activity`` — the paper trail for one record, newest first. Read-gated by the core
``activity.read`` permission; rows are org-scoped (RLS), so the feed can never cross tenants.
An unknown or non-auditable ``entity_type`` returns an empty feed rather than an error — a
client asking about a record with no trail yet is not a fault.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query

from app.core.activity.registry import is_auditable, read_permission_for
from app.core.activity.schemas import ActivityItem
from app.core.activity.service import ActivityService
from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context

router = APIRouter(prefix="/activity", tags=["activity"])

#: A panel is a summary, not a paginated log — it says so when it truncates (docs/UX.md).
MAX_LIMIT = 50


@router.get(
    "",
    response_model=list[ActivityItem],
    dependencies=[require_permission("activity.read")],
)
async def entity_activity(
    entity_type: str = Query(..., max_length=30),
    entity_id: uuid.UUID = Query(...),
    limit: int = Query(20, ge=1, le=MAX_LIMIT),
    ctx: RequestContext = Depends(require_context),
) -> list[ActivityItem]:
    if not is_auditable(entity_type):
        return []
    # A portal login reads dashboards, not the staff paper trail — the trail names actors,
    # edits and internal notes-adjacent payloads. Empty, not 403: the panel simply isn't there.
    if ctx.is_portal:
        return []
    # A record's trail is only readable by someone who may read the record itself (audit F7):
    # ``activity.read`` is a blanket grant, so require the entity's own module read permission on
    # top of it. Types that opted in without one fall back to ``activity.read`` alone.
    entity_read = read_permission_for(entity_type)
    if entity_read is not None:
        ctx.require(entity_read)
    items = await ActivityService(ctx).feed(entity_type, entity_id, limit)
    return [ActivityItem(**item) for item in items]
