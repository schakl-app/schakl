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
    action = "file_attached" if payload["action"] == "attached" else "file_removed"
    await ActivityService(ctx).record(
        "project", project.id, action, {"filename": payload.get("filename")}
    )
