"""React to file attach/remove events for companies (the image-attachment task).

Mirrors ``projects.attachments``: validates the target client exists and records the change on
the company's core activity trail (§16), in the uploading request's transaction — so a file
attached to a client id that is not ours fails the whole upload rather than storing an
orphan, and the trail says who pinned what.
"""

from __future__ import annotations

from typing import Any

from app.core.activity.service import ActivityService
from app.core.events import EmitContext
from app.errors import AppError
from app.modules.companies.models import Company

_ACTIONS = {
    "attached": "file_attached",
    "removed": "file_removed",
    "visibility": "file_visibility_changed",
}


async def on_file_event(ctx: EmitContext, payload: dict[str, Any]) -> None:
    if payload.get("entity_type") != "company":
        return
    company = await ctx.repo(Company).get(payload["entity_id"])
    if company is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    entry: dict[str, Any] = {"filename": payload.get("filename")}
    if "client_visible" in payload:
        entry["client_visible"] = payload["client_visible"]
    await ActivityService(ctx).record(
        "company", company.id, _ACTIONS[payload["action"]], entry
    )
