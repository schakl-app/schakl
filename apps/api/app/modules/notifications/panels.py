"""Activity panel on the company detail view (CLAUDE.md §6, the modular hub).

What happened to this client, most recent first — the same event log the inbox reads, but
recipient-independent: a colleague's action shows up here whether or not it notified you.

The panel hangs last (position 90): it is history, and history belongs under the working
surfaces, not above them.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.modules.notifications.events import ENTITY_COMPANY
from app.modules.notifications.service import NotificationService
from app.registry import PanelSpec

#: A panel is a summary, not a paginated log — it says so when it truncates (docs/UX.md).
PANEL_LIMIT = 10


async def _activity_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    items = await NotificationService(ctx).activity(ENTITY_COMPANY, company_id, PANEL_LIMIT)
    return {
        "items": [
            {
                "id": str(item["id"]),
                "event_type": item["event_type"],
                "entity_type": item["entity_type"],
                "entity_id": str(item["entity_id"]),
                "actor_name": item["actor_name"],
                "payload": item["payload"],
                "created_at": item["created_at"].isoformat(),
            }
            for item in items
        ],
        "limit": PANEL_LIMIT,
    }


notifications_company_panel = PanelSpec(
    key="notifications.activity",
    entity_type="company",
    title_key="notifications.activity.title",
    provider=_activity_provider,
    position=90,
)
