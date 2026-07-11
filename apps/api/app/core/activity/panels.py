"""The activity trail as a panel, contributed by core to every auditable host (issue #67).

The company hub composes API :class:`PanelSpec` providers (opaque dicts), so the company's
trail rides that seam. Projects and contacts compose typed ``EntityPanelSpec`` loads on the
web side instead; both read the same core ``/api/v1/activity`` feed. Registered as a *core*
panel (``registry.register_core_panel``), not a module's, because the trail is a platform
guarantee that does not belong to any one module.

The panel hangs last (position 90): it is history, and history belongs under the working
surfaces, not above them — the same position the notifications panel used before it.
"""

from __future__ import annotations

import uuid

from app.core.activity.service import ActivityService
from app.core.tenancy import RequestContext
from app.registry import PanelSpec, registry

#: A panel is a summary, not a paginated log — it says so when it truncates (docs/UX.md).
PANEL_LIMIT = 10


def _provider(entity_type: str):
    async def provide(ctx: RequestContext, entity_id: uuid.UUID) -> dict:
        items = await ActivityService(ctx).feed(entity_type, entity_id, PANEL_LIMIT)
        return {
            "items": [
                {
                    "id": str(item["id"]),
                    "action": item["action"],
                    "entity_type": item["entity_type"],
                    "entity_id": str(item["entity_id"]),
                    "actor_name": item["actor_name"],
                    "actor_deleted": item["actor_deleted"],
                    "payload": item["payload"],
                    "created_at": item["created_at"].isoformat(),
                }
                for item in items
            ],
            "limit": PANEL_LIMIT,
        }

    return provide


def register_core_activity_panels() -> None:
    """Idempotent-enough at import: register the company hub's activity panel once."""
    registry.register_core_panel(
        PanelSpec(
            key="activity.trail",
            entity_type="company",
            title_key="activity.title",
            provider=_provider("company"),
            position=90,
        )
    )


register_core_activity_panels()
