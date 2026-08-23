"""Drive endpoints under ``/api/v1/google/drive`` (issue #21)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.google.drive.service import MAX_BROWSE_QUERY, DriveService

router = APIRouter(prefix="/drive", tags=["google"])


class DriveBrowseFolder(BaseModel):
    id: str | None = None
    name: str | None = None
    web_view_link: str | None = None


class DriveBrowseItem(BaseModel):
    id: str
    name: str
    mime_type: str | None = None
    is_folder: bool = False
    web_view_link: str | None = None
    modified_at: str | None = None
    size: int | None = None


class DriveBrowseResult(BaseModel):
    folder: DriveBrowseFolder
    items: list[DriveBrowseItem]
    #: The search term this list answers, echoed so the screen can name it (`null` = the
    #: folder's own contents).
    query: str | None = None
    #: Drive had another page we did not follow — the list is a prefix, and says so (#336).
    truncated: bool = False


class DriveLinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    entity_type: str
    entity_id: uuid.UUID
    drive_file_id: str
    drive_url: str
    name: str
    mime_type: str | None = None
    is_folder: bool = False
    #: This link *is* the record's folder (at most one per record) — not merely a folder.
    is_root: bool = False
    created_by_name: str | None = None


class DriveLinkCreate(BaseModel):
    entity_type: str = Field(..., pattern="^(company|project|task)$")
    entity_id: uuid.UUID
    drive_file_id: str = Field(..., min_length=1, max_length=128)


class DriveFolderSet(BaseModel):
    entity_type: str = Field(..., pattern="^(company|project|task)$")
    entity_id: uuid.UUID
    #: An **existing** Drive folder, chosen in the browser.
    drive_file_id: str = Field(..., min_length=1, max_length=128)


class DriveUploadSessionCreate(BaseModel):
    folder_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=500)
    mime_type: str | None = Field(default=None, max_length=255)


class DriveUploadSession(BaseModel):
    #: The browser PUTs the file bytes straight here — never through this API (issue #21).
    session_uri: str


class DriveFolderCreate(BaseModel):
    #: Where the new folder is created — the folder currently being browsed.
    parent_id: str = Field(..., min_length=1, max_length=128)
    name: str = Field(..., min_length=1, max_length=255)


class DriveFolder(BaseModel):
    id: str
    name: str
    web_view_link: str | None = None


class DriveProvisionRequest(BaseModel):
    #: Every entity a Drive folder can hang off (#328). Auto-provisioning still covers only
    #: companies and projects — tasks are numerous and short-lived, so a task's folder is
    #: always somebody pressing the button on the panel.
    entity_type: str = Field(..., pattern="^(company|project|task)$")
    entity_id: uuid.UUID


class DriveBulkProvisionResult(BaseModel):
    queued: int


@router.get(
    "/browse",
    response_model=DriveBrowseResult,
    dependencies=[require_permission("google.drive.read")],
)
async def browse(
    folder_id: str | None = Query(None, max_length=128),
    q: str | None = Query(None, max_length=MAX_BROWSE_QUERY),
    refresh: bool = Query(False),
    ctx: RequestContext = Depends(require_context),
) -> DriveBrowseResult:
    """Live folder contents **as the viewing user** — Drive's permissions are authoritative.

    ``q`` filters by name **inside this folder**, at Drive rather than in the browser (#336):
    the listing is one page of 100, so a client-side filter would answer "nothing found" for
    the 101st file. Redis-cached ~45 s per user+folder+term; ``refresh=1`` busts it.
    """
    listing = await DriveService(ctx).browse(folder_id, q=q, refresh=refresh)
    return DriveBrowseResult(**listing)


@router.get(
    "/links",
    response_model=list[DriveLinkRead],
    dependencies=[require_permission("google.drive.read")],
)
async def list_links(
    entity_type: str = Query(..., max_length=32),
    entity_id: uuid.UUID = Query(...),
    rollup: bool = Query(False),
    limit: int | None = Query(
        None,
        ge=1,
        le=200,
        description=(
            "Cap the page a panel draws (#407); ask for one more than you keep to learn "
            "there are more. Absent means every link, which is what the roll-up view needs — "
            "a roll-up folds a project's tasks in after the query and cannot be cut before it."
        ),
    ),
    ctx: RequestContext = Depends(require_context),
) -> list[DriveLinkRead]:
    links = await DriveService(ctx).links_for(
        entity_type, entity_id, rollup=rollup, limit=limit
    )
    return [DriveLinkRead.model_validate(link) for link in links]


@router.post(
    "/links",
    response_model=DriveLinkRead,
    status_code=201,
    dependencies=[require_permission("google.drive.write")],
)
async def create_link(
    payload: DriveLinkCreate,
    ctx: RequestContext = Depends(require_context),
) -> DriveLinkRead:
    link = await DriveService(ctx).create_link(
        payload.entity_type, payload.entity_id, payload.drive_file_id
    )
    return DriveLinkRead.model_validate(link)


@router.put(
    "/folder",
    response_model=DriveLinkRead,
    dependencies=[require_permission("google.drive.write")],
)
async def set_folder(
    payload: DriveFolderSet,
    ctx: RequestContext = Depends(require_context),
) -> DriveLinkRead:
    """Point a record at an existing Drive folder — the picker's target.

    The declared key is the base one, so deny-by-default stays enumerable; the service adds
    ``google.drive.manage`` when the record **already has** a folder, because re-pointing one
    is a different act from giving it its first (CLAUDE.md §15's two layers).
    """
    link = await DriveService(ctx).set_folder(
        payload.entity_type, payload.entity_id, payload.drive_file_id
    )
    return DriveLinkRead.model_validate(link)


@router.delete(
    "/links/{link_id}",
    status_code=204,
    dependencies=[require_permission("google.drive.write")],
)
async def delete_link(
    link_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Unlink only. The Drive file is never touched (issue #21 — the dialog says so too)."""
    await DriveService(ctx).delete_link(link_id)


@router.delete(
    "/files/{drive_file_id}",
    status_code=204,
    dependencies=[require_permission("google.drive.write")],
)
async def trash_file(
    drive_file_id: str = Path(..., min_length=1, max_length=128),
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Move a Drive file to Drive's bin — the other half of unlink (#394).

    Unlink says "this file is not about this record" and touches nothing in Drive; this says
    "this file should not exist". Two acts, two controls, deliberately never one.

    It runs **as the viewing user**, like ``browse``: Drive's permissions decide, so a
    colleague who could not delete the file in Drive gets Google's own refusal. Every
    ``drive_links`` row naming the file goes with it, org-wide, in the same transaction, and a
    non-empty folder is refused. Nothing is ever purged — Drive's bin keeps it for 30 days.
    """
    await DriveService(ctx).trash_file(drive_file_id)


@router.post(
    "/upload-session",
    response_model=DriveUploadSession,
    dependencies=[require_permission("google.drive.write")],
)
async def create_upload_session(
    payload: DriveUploadSessionCreate,
    request: Request,
    ctx: RequestContext = Depends(require_context),
) -> DriveUploadSession:
    session_uri = await DriveService(ctx).upload_session(
        payload.folder_id,
        payload.name,
        payload.mime_type,
        request.headers.get("origin"),
    )
    return DriveUploadSession(session_uri=session_uri)


@router.post(
    "/folders",
    response_model=DriveFolder,
    status_code=201,
    dependencies=[require_permission("google.drive.write")],
)
async def create_folder(
    payload: DriveFolderCreate,
    ctx: RequestContext = Depends(require_context),
) -> DriveFolder:
    """Create a subfolder inside the folder being browsed, as the viewing user (issue #21)."""
    folder = await DriveService(ctx).create_folder(payload.parent_id, payload.name)
    return DriveFolder(**folder)


@router.post(
    "/provision",
    status_code=202,
    dependencies=[require_permission("google.drive.write")],
)
async def provision_entity(
    payload: DriveProvisionRequest,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Queue one entity's folder — the panel's "create folder" button."""
    await DriveService(ctx).request_provision(payload.entity_type, payload.entity_id)


@router.post(
    "/provision-all",
    response_model=DriveBulkProvisionResult,
    dependencies=[require_permission("google.settings.manage")],
)
async def provision_all(
    ctx: RequestContext = Depends(require_context),
) -> DriveBulkProvisionResult:
    """Backfill: a folder for every client that has none (Instellingen → Google)."""
    return DriveBulkProvisionResult(queued=await DriveService(ctx).bulk_provision())
