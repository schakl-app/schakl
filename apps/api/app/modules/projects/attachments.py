"""React to file attach/remove events for projects (issue #123, follow-up).

Mirrors ``tasks.attachments``: validates the target project exists and records the change on
the project's core activity trail (§16), in the uploading request's transaction.
"""

from __future__ import annotations

from typing import Any

from app.core.activity.service import ActivityService
from app.core.events import EmitContext
from app.errors import AppError
from app.modules.projects.models import Project


async def on_file_event(ctx: EmitContext, payload: dict[str, Any]) -> None:
    if payload.get("entity_type") != "project":
        return
    project = await ctx.repo(Project).get(payload["entity_id"])
    if project is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    action = _ACTIONS[payload["action"]]
    entry: dict[str, Any] = {"filename": payload.get("filename")}
    if "client_visible" in payload:
        entry["client_visible"] = payload["client_visible"]
    await ActivityService(ctx).record("project", project.id, action, entry)


_ACTIONS = {
    "attached": "file_attached",
    "removed": "file_removed",
    # Ticking "the client may see this" is a change to what the client reads, and the trail
    # is where "who showed the client that screenshot" is answered.
    "visibility": "file_visibility_changed",
}
