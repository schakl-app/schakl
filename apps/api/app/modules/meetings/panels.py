"""The meetings panel on the company hub (§6): the last few meetings with this client."""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.meetings.service import MeetingService
from app.registry import PROMINENCE_REGISTER, SIZE_HALF, PanelSpec

PANEL_LIMIT = 5


async def _meetings_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    page = await MeetingService(ctx).list(limit=PANEL_LIMIT, offset=0, company_id=company_id)
    return {
        "items": [row.model_dump(mode="json") for row in page.items],
        "total": page.total or 0,
    }


meetings_company_panel = PanelSpec(
    key="meetings.company",
    entity_type="company",
    title_key="meetings.panel.title",
    provider=_meetings_provider,
    position=38,
    requires_permission="meetings.meeting.read",
    prominence=PROMINENCE_REGISTER,
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("items"),
)
