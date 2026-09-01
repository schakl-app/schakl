"""React to file attach/remove events for tasks (issue #123, follow-up).

Core storage emits ``file.attached`` / ``file.removed`` on the in-process bus (§6); this
module validates the target task exists and writes the task's own activity trail. Handlers
run in the uploading request's transaction, so a file row and its activity line commit
atomically — and attaching to a task id that does not exist fails the whole upload.
"""

from __future__ import annotations

from typing import Any

from app.core.events import EmitContext
from app.errors import AppError
from app.modules.tasks.models import Task, TaskActivity


async def on_file_event(ctx: EmitContext, payload: dict[str, Any]) -> None:
    if payload.get("entity_type") != "task":
        return
    task = await ctx.repo(Task).get(payload["entity_id"])
    if task is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    action = _ACTIONS[payload["action"]]
    actor = ctx.user
    # An attachment can be added through an impersonated session too (#296); ``getattr`` because
    # a ``SystemContext`` has no such field.
    impersonator = getattr(ctx, "impersonated_by", None)
    ctx.session.add(
        TaskActivity(
            org_id=ctx.org.id,
            task_id=task.id,
            actor_user_id=actor.id if actor else None,
            # Snapshotted, so deleting the account doesn't hand this line to "System" (#64).
            actor_name=(actor.full_name or actor.email) if actor else None,
            impersonator_user_id=impersonator.id if impersonator else None,
            impersonator_name=(
                (impersonator.full_name or impersonator.email) if impersonator else None
            ),
            action=action,
            payload={
                "filename": payload.get("filename"),
                **(
                    {"client_visible": payload["client_visible"]}
                    if "client_visible" in payload
                    else {}
                ),
            },
        )
    )
    await ctx.session.flush()


_ACTIONS = {
    "attached": "attachment_added",
    "removed": "attachment_deleted",
    # Showing a client a screenshot is a change to what the client reads (#193's rule that a
    # task's visibility is the agency's decision, one level down), so the trail records it.
    "visibility": "attachment_visibility_changed",
}
