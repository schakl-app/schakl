"""Documents as a panel on the company hub, contributed by core (the image-attachment task).

Tasks and projects already rendered their attachments (#123); the client — the hub every
other record hangs off — had nowhere to pin a logo proof, a signed quote or the screenshot of
the site as delivered, so those went to Drive or stayed in somebody's mailbox. The hub
composes API :class:`PanelSpec` providers, so the company's documents ride that seam exactly as
the activity trail does (``core/activity/panels.py``): registered as a *core* panel, because
storing a file against a record is a platform capability and not a module's.

A register (#364): where the files are is looked up when somebody needs one, never news.
"""

from __future__ import annotations

import uuid

from app.core.storage.schemas import StoredFileRead
from app.core.storage.service import FileService
from app.core.tenancy import RequestContext
from app.registry import SIZE_HALF, PanelSpec, registry


async def _provide(ctx: RequestContext, company_id: uuid.UUID) -> dict:
    rows = await FileService(ctx).list_for("company", company_id)
    return {"items": [StoredFileRead.model_validate(row).model_dump(mode="json") for row in rows]}


def register_core_storage_panels() -> None:
    registry.register_core_panel(
        PanelSpec(
            key="files.documents",
            entity_type="company",
            title_key="files.title",
            provider=_provide,
            # Under the working surfaces, above the trail: a document is consulted more often
            # than the change history and less often than an open task.
            position=80,
            requires_permission=None,
            explicit_public=(
                "the same gate as GET /files: any member who may open the client may read its "
                "documents (the rows are RLS-scoped and horizon-checked by the hub route), and a "
                "client-portal login sees only the files the agency ticked visible — the service "
                "applies files.client_visible per row"
            ),
            size=SIZE_HALF,
            empty_when=lambda data: not data.get("items"),
        )
    )


register_core_storage_panels()
