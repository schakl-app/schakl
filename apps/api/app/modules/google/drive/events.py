"""Event handlers: a new client (or project) gets its folder in the shared drive (#21).

In-transaction and write-only, like every bus handler: the folder job row commits with the
company/project itself; the worker does the Drive I/O with the org's automation connection.
"""

from __future__ import annotations

from typing import Any

from app.core.events import EmitContext
from app.modules.google.drive.service import drive_root, queue_folder_job
from app.modules.google.oauth import google_settings_row


async def _provisioning_on(ctx: EmitContext) -> bool:
    row = await google_settings_row(ctx.session, ctx.org.id)
    return bool(
        row is not None
        and row.drive_enabled
        and row.drive_auto_provision
        and row.automation_connection_user_id
        # Same parent-resolution rule as the manual button and the backfill (#149, #260):
        # a shared drive alone is enough — its id is a valid root. Not ``drive_parent_folder_id``
        # alone, which silently no-ops automatic provisioning for shared-drive-only orgs.
        and drive_root(row) is not None
    )


async def handle_company_created(ctx: EmitContext, payload: dict[str, Any]) -> None:
    # ``title`` carries the company name on the bus (the notifications feed contract).
    company_id, name = payload.get("company_id"), payload.get("title")
    if not company_id or not name or not await _provisioning_on(ctx):
        return
    await queue_folder_job(ctx.session, ctx.org.id, "company", company_id, str(name))


async def handle_project_created(ctx: EmitContext, payload: dict[str, Any]) -> None:
    project_id, name = payload.get("project_id"), payload.get("name")
    if not project_id or not name or not await _provisioning_on(ctx):
        return
    # Nested under the client's folder when the client has one (resolved at execution time).
    await queue_folder_job(
        ctx.session,
        ctx.org.id,
        "project",
        project_id,
        str(name),
        parent_entity_id=payload.get("company_id"),
    )
