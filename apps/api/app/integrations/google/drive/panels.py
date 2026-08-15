"""Drive panel on the company detail view (issue #21 — the modular hub, §6).

Deliberately links-only: the SSR panel load must never wait on a Google round trip
(docs/PERFORMANCE.md). The embedded browser fetches its live listing from the browser after
mount, against the Redis-cached ``/google/drive/browse`` endpoint, as the viewing user.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.integrations.google.drive.models import DriveLink
from app.integrations.google.drive.service import DriveService
from app.integrations.google.oauth import google_settings_row
from app.registry import SIZE_HALF, PanelSpec


def _present(link: DriveLink) -> dict:
    return {
        "id": str(link.id),
        "drive_file_id": link.drive_file_id,
        "drive_url": link.drive_url,
        "name": link.name,
        "mime_type": link.mime_type,
        "is_folder": link.is_folder,
        "is_root": link.is_root,
        "created_by_name": link.created_by_name,
    }


async def _drive_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("google.drive.read"):
        return {"links": [], "forbidden": True}
    row = await google_settings_row(ctx.session, ctx.org.id)
    if row is None or not row.drive_enabled:
        return {"links": [], "disabled": True}
    links = await DriveService(ctx).links_for("company", company_id)
    # The client's folder is the one somebody decided on, never the first folder that happens
    # to be linked here — a subfolder linked as an attachment must not hijack the panel.
    folder = next((link for link in links if link.is_root), None)
    from app.integrations.google.client import connection_for
    from app.integrations.google.models import ConnectionStatus

    connection = await connection_for(ctx.session, ctx.org.id, ctx.user.id)
    return {
        "links": [_present(link) for link in links],
        "folder": _present(folder) if folder else None,
        # The browser can only list as a connected viewer; the panel says so instead of erroring.
        "viewer_connected": bool(
            connection and connection.status == ConnectionStatus.ACTIVE.value
        ),
        "can_provision": bool(row.automation_connection_user_id and ctx.can("google.drive.write")),
        # Choosing the client's *first* folder is ordinary write work; changing or detaching an
        # existing one is not. The panel draws each control on the gate that will answer it —
        # never a lock the API would then contradict.
        "can_pick": ctx.can("google.drive.write"),
        "can_manage": ctx.can("google.drive.manage"),
    }


drive_company_panel = PanelSpec(
    key="google.drive.company",
    entity_type="company",
    title_key="google.drive.panel.title",
    provider=_drive_provider,
    position=55,
    # No declaration on purpose (#365): every control on this panel already states its own gate
    # (`can_pick`, `can_manage`, `viewer_connected`), and "connect your Google account" is a
    # refusal the reader can act on rather than one to hide from them.
    explicit_public="draws its own connect/permission states; every control self-gates",
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("folder") and not data.get("links"),
)
