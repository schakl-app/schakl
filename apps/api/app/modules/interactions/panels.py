"""Contactmomenten panel on the company detail view (the modular hub, §6).

Projects, contacts and tasks get theirs through the web ``EntityPanelSpec`` seam instead —
same split the core activity trail uses.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.interactions.service import InteractionService
from app.registry import PanelSpec

PANEL_LIMIT = 8


async def _interactions_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("interactions.interaction.read"):
        return {"items": [], "total": 0, "forbidden": True}
    items, total = await InteractionService(ctx).list(
        limit=PANEL_LIMIT, offset=0, company_id=company_id
    )
    return {
        "items": [
            {
                "id": str(i["id"]),
                "kind": i["kind"],
                "status": i["status"],
                "occurred_at": i["occurred_at"].isoformat(),
                "subject": i["subject"],
                "snippet": i["snippet"],
                "body_text": i["body_text"],
                "direction": i["direction"],
                # Links + labels (#147): the move dialog prefills from these and the row
                # chips deep-link through them — labels resolved by the service, batched.
                "company_id": str(i["company_id"]) if i["company_id"] else None,
                "project_id": str(i["project_id"]) if i["project_id"] else None,
                "task_id": str(i["task_id"]) if i["task_id"] else None,
                "contact_id": str(i["contact_id"]) if i["contact_id"] else None,
                "company_name": i["company_name"],
                "project_name": i["project_name"],
                "task_title": i["task_title"],
                "contact_name": i["contact_name"],
                "owner_user_id": str(i["owner_user_id"]) if i["owner_user_id"] else None,
                "owner_name": i["owner_name"],
                "participants": i["participants"],
                "source": i["source"],
                "deep_link": i["deep_link"],
            }
            for i in items
        ],
        "total": total,
        "current_user_id": str(ctx.user.id),
    }


interactions_company_panel = PanelSpec(
    key="interactions.company",
    entity_type="company",
    title_key="interactions.panel.title",
    provider=_interactions_provider,
    # Right under the working surfaces (contacts/projects/tasks): the communication timeline
    # is daily-use, unlike the asset panels (websites/domains) that sit near the bottom.
    position=35,
)
