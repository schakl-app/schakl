"""OneDrive endpoints under ``/api/v1/microsoft/onedrive`` (docs/MICROSOFT.md §5)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, ConfigDict, Field

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.microsoft.onedrive.service import MAX_BROWSE_QUERY, OneDriveService

router = APIRouter(prefix="/onedrive", tags=["microsoft"])

_ENTITY_PATTERN = "^(company|project|task)$"


class OneDriveBrowseFolder(BaseModel):
    id: str | None = None
    drive_id: str | None = None
    name: str | None = None
    web_view_link: str | None = None


class OneDriveBrowseItem(BaseModel):
    id: str
    drive_id: str
    name: str
    mime_type: str | None = None
    is_folder: bool = False
    web_view_link: str | None = None
    modified_at: str | None = None
    size: int | None = None


class OneDriveBrowseResult(BaseModel):
    folder: OneDriveBrowseFolder
    items: list[OneDriveBrowseItem]
    #: The search term this list answers, echoed so the screen can name it (`null` = the
    #: folder's own contents). A search covers the folder's whole subtree at Graph.
    query: str | None = None
    #: Graph had another page we did not follow — the list is a prefix, and says so.
    truncated: bool = False


class OneDriveLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    drive_id: str
    item_id: str
    web_url: str
    name: str
    mime_type: str | None = None
    is_folder: bool = False
    #: This link *is* the record's folder (at most one per record) — not merely a folder.
    is_root: bool = False
    created_by_name: str | None = None


class OneDriveLinkCreate(BaseModel):
    entity_type: str = Field(..., pattern=_ENTITY_PATTERN)
    entity_id: uuid.UUID
    drive_id: str = Field(..., min_length=1, max_length=256)
    item_id: str = Field(..., min_length=1, max_length=256)


class OneDriveFolderSet(BaseModel):
    entity_type: str = Field(..., pattern=_ENTITY_PATTERN)
    entity_id: uuid.UUID
    #: An **existing** folder, chosen in the browser.
    drive_id: str = Field(..., min_length=1, max_length=256)
    item_id: str = Field(..., min_length=1, max_length=256)


class OneDriveUploadSessionCreate(BaseModel):
    drive_id: str = Field(..., min_length=1, max_length=256)
    folder_id: str = Field(..., min_length=1, max_length=256)
    name: str = Field(..., min_length=1, max_length=500)
    mime_type: str | None = Field(default=None, max_length=255)


class OneDriveUploadSession(BaseModel):
    #: The browser PUTs the file bytes straight here (``Content-Range`` per fragment) — never
    #: through this API. The URL is pre-authenticated by Graph.
    session_uri: str


class OneDriveFolderCreate(BaseModel):
    drive_id: str = Field(..., min_length=1, max_length=256)
    #: Where the new folder is created — the folder currently being browsed.
    parent_id: str = Field(..., min_length=1, max_length=256)
    name: str = Field(..., min_length=1, max_length=255)


class OneDriveFolder(BaseModel):
    id: str
    drive_id: str
    name: str
    web_view_link: str | None = None


class OneDriveProvisionRequest(BaseModel):
    entity_type: str = Field(..., pattern=_ENTITY_PATTERN)
    entity_id: uuid.UUID


class OneDriveBulkProvisionResult(BaseModel):
    queued: int


class OneDriveStateRead(BaseModel):
    """Provisioning readiness for the panels (#444's rule)."""

    enabled: bool
    viewer_connected: bool
    can_provision: bool
    job_status: str | None = None
    #: Graph's own sentence for a failed job — an admin-facing read, never an i18n key.
    job_error: str | None = None


@router.get(
    "/browse",
    response_model=OneDriveBrowseResult,
    dependencies=[require_permission("microsoft.onedrive.read")],
)
async def browse(
    drive_id: str | None = Query(None, max_length=256),
    folder_id: str | None = Query(None, max_length=256),
    q: str | None = Query(None, max_length=MAX_BROWSE_QUERY),
    refresh: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> OneDriveBrowseResult:
    """Live folder contents **as the viewing user** — Graph's permissions are authoritative.

    ``q`` searches at Graph (the folder's subtree), never in the browser over one capped page.
    Redis-cached ~45 s per user+drive+folder+term; ``refresh=1`` busts it.
    """
    listing = await OneDriveService(ctx).browse(drive_id, folder_id, q=q, refresh=refresh)
    return OneDriveBrowseResult(**listing)


@router.get(
    "/state",
    response_model=OneDriveStateRead,
    dependencies=[require_permission("microsoft.onedrive.read")],
)
async def onedrive_state(
    entity_type: str | None = Query(None, max_length=32),
    entity_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> OneDriveStateRead:
    return OneDriveStateRead(**await OneDriveService(ctx).provision_state(entity_type, entity_id))


@router.get(
    "/links",
    response_model=list[OneDriveLinkRead],
    dependencies=[require_permission("microsoft.onedrive.read")],
)
async def list_links(
    entity_type: str = Query(..., max_length=32),
    entity_id: uuid.UUID = Query(...),
    rollup: bool = Query(False),
    limit: int | None = Query(None, ge=1, le=200),
    ctx: RequestContext = Depends(require_context),
) -> list[OneDriveLinkRead]:
    links = await OneDriveService(ctx).links_for(entity_type, entity_id, rollup=rollup, limit=limit)
    return [OneDriveLinkRead.model_validate(link) for link in links]


@router.post(
    "/links",
    response_model=OneDriveLinkRead,
    status_code=201,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def create_link(
    payload: OneDriveLinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> OneDriveLinkRead:
    link = await OneDriveService(ctx).create_link(
        payload.entity_type, payload.entity_id, payload.drive_id, payload.item_id
    )
    return OneDriveLinkRead.model_validate(link)


@router.put(
    "/folder",
    response_model=OneDriveLinkRead,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def set_folder(
    payload: OneDriveFolderSet,
    ctx: RequestContext = Depends(require_context),
) -> OneDriveLinkRead:
    """Point a record at an existing folder — the picker's target. The service adds
    ``microsoft.onedrive.manage`` when the record **already has** a folder (§15's two layers)."""
    link = await OneDriveService(ctx).set_folder(
        payload.entity_type, payload.entity_id, payload.drive_id, payload.item_id
    )
    return OneDriveLinkRead.model_validate(link)


@router.delete(
    "/links/{link_id}",
    status_code=204,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def delete_link(
    link_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Unlink only. The item is never touched (the dialog says so too)."""
    await OneDriveService(ctx).delete_link(link_id)


@router.delete(
    "/files/{drive_id}/{item_id}",
    status_code=204,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def trash_item(
    drive_id: str = Path(..., min_length=1, max_length=256),
    item_id: str = Path(..., min_length=1, max_length=256),
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Move an item to the drive's recycle bin — the other half of unlink. Runs as the viewing
    user; drops every link naming the item, org-wide; refuses a non-empty folder."""
    await OneDriveService(ctx).trash_item(drive_id, item_id)


@router.post(
    "/upload-session",
    response_model=OneDriveUploadSession,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def create_upload_session(
    payload: OneDriveUploadSessionCreate,
    ctx: RequestContext = Depends(require_context),
) -> OneDriveUploadSession:
    session_uri = await OneDriveService(ctx).upload_session(
        payload.drive_id, payload.folder_id, payload.name, payload.mime_type
    )
    return OneDriveUploadSession(session_uri=session_uri)


@router.post(
    "/folders",
    response_model=OneDriveFolder,
    status_code=201,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def create_folder(
    payload: OneDriveFolderCreate,
    ctx: RequestContext = Depends(require_context),
) -> OneDriveFolder:
    """Create a subfolder inside the folder being browsed, as the viewing user."""
    folder = await OneDriveService(ctx).create_folder(
        payload.drive_id, payload.parent_id, payload.name
    )
    return OneDriveFolder(**folder)


@router.post(
    "/provision",
    status_code=202,
    dependencies=[require_permission("microsoft.onedrive.write")],
)
async def provision_entity(
    payload: OneDriveProvisionRequest,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Queue one entity's folder — the panel's "create folder" button."""
    await OneDriveService(ctx).request_provision(payload.entity_type, payload.entity_id)


@router.post(
    "/provision-all",
    response_model=OneDriveBulkProvisionResult,
    dependencies=[require_permission("microsoft.settings.manage")],
)
async def provision_all(
    ctx: RequestContext = Depends(require_context),
) -> OneDriveBulkProvisionResult:
    """Backfill: a folder for every client that has none (Instellingen → Microsoft 365)."""
    return OneDriveBulkProvisionResult(queued=await OneDriveService(ctx).bulk_provision())
