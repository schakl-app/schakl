"""OneDrive panel on the company detail view (the modular hub, §6).

Deliberately links-only: the SSR panel load must never wait on a Graph round trip
(docs/PERFORMANCE.md). The embedded browser fetches its live listing from the browser after
mount, against the Redis-cached ``/microsoft/onedrive/browse`` endpoint, as the viewing user.
Same dict shape as the Drive panel, so the two web panels are one component's worth apart.
"""

from __future__ import annotations

import uuid

from app.core.tenancy import RequestContext
from app.integrations.microsoft.client import connection_for
from app.integrations.microsoft.models import ConnectionStatus
from app.integrations.microsoft.oauth import microsoft_settings_row
from app.integrations.microsoft.onedrive.models import OneDriveLink
from app.integrations.microsoft.onedrive.service import OneDriveService, drive_root
from app.registry import PANEL_FEED, SIZE_HALF, PanelSpec


def _present(link: OneDriveLink) -> dict:
    return {
        "id": str(link.id),
        "drive_id": link.drive_id,
        "item_id": link.item_id,
        "web_url": link.web_url,
        "name": link.name,
        "mime_type": link.mime_type,
        "is_folder": link.is_folder,
        "is_root": link.is_root,
        "created_by_name": link.created_by_name,
    }


async def _onedrive_provider(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    if not ctx.can("microsoft.onedrive.read"):
        return {"links": [], "forbidden": True}
    row = await microsoft_settings_row(ctx.session, ctx.org.id)
    if row is None or not row.onedrive_enabled:
        return {"links": [], "disabled": True}
    service = OneDriveService(ctx)
    # Bounded, and the whole count beside it (#407).
    links = await service.links_for("company", company_id, limit=PANEL_FEED)
    total = (
        await service.count_links("company", company_id)
        if len(links) >= PANEL_FEED
        else len(links)
    )
    # The client's folder is the one somebody decided on, never the first folder linked here.
    folder = next((link for link in links if link.is_root), None)
    connection = await connection_for(ctx.session, ctx.org.id, ctx.user.id)
    return {
        "total": total,
        "links": [_present(link) for link in links],
        "folder": _present(folder) if folder else None,
        # The browser can only list as a connected viewer; the panel says so instead of erroring.
        "viewer_connected": bool(
            connection and connection.status == ConnectionStatus.ACTIVE.value
        ),
        # Everything the 409s downstream would refuse on: the automation connection AND a
        # configured drive — a button drawn on half the requirement can only refuse (#253).
        "can_provision": bool(
            row.automation_connection_user_id
            and drive_root(row)
            and ctx.can("microsoft.onedrive.write")
        ),
        "can_pick": ctx.can("microsoft.onedrive.write"),
        "can_manage": ctx.can("microsoft.onedrive.manage"),
    }


onedrive_company_panel = PanelSpec(
    key="microsoft.onedrive.company",
    entity_type="company",
    title_key="microsoft.onedrive.panel.title",
    provider=_onedrive_provider,
    position=56,
    # No declaration on purpose (#365): every control on this panel already states its own gate
    # (`can_pick`, `can_manage`, `viewer_connected`), and "connect your Microsoft account" is a
    # refusal the reader can act on rather than one to hide from them.
    explicit_public="draws its own connect/permission states; every control self-gates",
    size=SIZE_HALF,
    empty_when=lambda data: not data.get("folder") and not data.get("links"),
)
