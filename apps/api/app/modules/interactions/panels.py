"""Contactmomenten panel on the company detail view (the modular hub, §6).

Projects, contacts and tasks get theirs through the web ``EntityPanelSpec`` seam instead —
same split the core activity trail uses.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.interactions.service import InteractionService
from app.registry import PROMINENCE_PRIMARY, SIZE_HALF, PanelSpec

PANEL_LIMIT = 8


async def _interactions_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    # The permission is declared on the spec (#365); the composer is the gate.
    # ``count`` stays on: the panel footer says "8 of 214", so the total is rendered and
    # skipping it would be a lie, not a saving. ``with_body`` stays off (the default): the
    # panel draws snippets, and the detail modal fetches the row it opens (#290).
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
                # Everyone the moment was with (#300) — the panel draws a chip per person and
                # the edit/move dialogs prefill the roster from these.
                "contacts": [
                    {"id": str(c["id"]), "name": c["name"]} for c in i["contacts"]
                ],
                "company_name": i["company_name"],
                "project_name": i["project_name"],
                "task_title": i["task_title"],
                "contact_name": i["contact_name"],
                "owner_user_id": str(i["owner_user_id"]) if i["owner_user_id"] else None,
                "owner_name": i["owner_name"],
                "participants": i["participants"],
                "source": i["source"],
                # Gmail-style conversation grouping (#272): the folded row's message count drives
                # the badge and whether the detail modal fetches the whole thread.
                "conversation_id": str(i["conversation_id"]) if i["conversation_id"] else None,
                "conversation_count": i["conversation_count"],
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
    requires_permission="interactions.interaction.read",
    prominence=PROMINENCE_PRIMARY,
    # Half width, like every other working panel: the timeline is a feed of short rows, and at
    # full width it pushed everything after it down a storey.
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("items"),
)
