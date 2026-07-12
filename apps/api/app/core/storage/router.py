"""REST surface for stored files (issue #123): upload + guarded serving.

Files are served **through the API, never raw-static**, so tenant scoping and access control
apply — a task attachment can be sensitive. `GET` streams with an ETag (the immutable file id)
and honours `If-None-Match`, so a repeat avatar fetch costs a 304.

Branding assets (logo/favicon) are the one anonymous exception: they render on the login
screen before a session exists, so `GET /files/{id}/public` serves them with the org resolved
from the hostname alone — and reaches *only* rows tagged with a public entity type.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.core.permissions.deps import no_permission_required, require_permission
from app.core.storage.backend import get_storage
from app.core.storage.models import StoredFile
from app.core.storage.schemas import StoredFileRead
from app.core.storage.service import PUBLIC_ENTITY_TYPES, FileService
from app.core.tenancy import RequestContext, request_hostname, require_context, resolve_org
from app.db import async_session_maker, set_current_org
from app.errors import AppError

router = APIRouter(prefix="/files", tags=["files"])

#: Types a browser may render inline; anything else downloads. SVG is deliberately NOT inline —
#: an inline SVG executes script in the serving origin, which would be a stored-XSS hole.
_INLINE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
)


def _file_response(stored: StoredFile, request: Request, *, public: bool = False) -> Response:
    """Stream a stored file with ETag/304, honouring the inline allow-list."""
    etag = f'"{stored.id}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    disposition = "inline" if stored.content_type in _INLINE_TYPES else "attachment"
    try:
        stream = get_storage().open(stored.storage_key)
    except FileNotFoundError:
        # A row without bytes: the backend lost them (restored DB without the volume).
        raise AppError("not_found", "errors.not_found", status_code=404) from None
    filename = stored.filename.replace('"', "")
    cache = "public, max-age=3600" if public else "private, max-age=3600"
    return StreamingResponse(
        stream,
        media_type=stored.content_type,
        headers={
            "ETag": etag,
            "Cache-Control": cache,
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Length": str(stored.size_bytes),
        },
    )


@router.post(
    "",
    response_model=StoredFileRead,
    status_code=201,
    dependencies=[require_permission("files.file.write")],
)
async def upload_file(
    file: UploadFile,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    ctx: RequestContext = Depends(require_context),
) -> StoredFileRead:
    """Multipart upload. Size and content type are bounded by instance config."""
    # UploadFile is already spooled to disk past a small threshold; size it without trusting
    # the client's Content-Length.
    file.file.seek(0, 2)
    size = file.file.tell()
    file.file.seek(0)
    stored = await FileService(ctx).create(
        filename=file.filename or "file",
        content_type=file.content_type or "application/octet-stream",
        stream=file.file,
        size_bytes=size,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    return StoredFileRead.model_validate(stored)


@router.get(
    "/{file_id}",
    dependencies=[
        no_permission_required(
            "any signed-in member may fetch their tenant's files; the row is RLS-scoped"
        )
    ],
)
async def serve_file(
    file_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """Stream the bytes. Cross-tenant ids read as 404 (tenant-scoped row lookup)."""
    stored = await FileService(ctx).get_or_404(file_id)
    return _file_response(stored, request)


@router.get(
    "/{file_id}/public",
    dependencies=[
        no_permission_required(
            "branding assets (logo/favicon) render on the login screen before a session "
            "exists; only rows tagged with a public entity type are reachable here"
        )
    ],
)
async def serve_public_file(file_id: uuid.UUID, request: Request) -> Response:
    """Anonymous serving for branding assets, org resolved strictly from the hostname.

    Suspended orgs still resolve (their login screen keeps its branding, matching
    `/meta/tenant`); deleted orgs — and any unknown host — read as 404.
    """
    async with async_session_maker() as session:
        org = await resolve_org(session, request_hostname(request))
        if org is None:
            raise AppError("unknown_host", "errors.unknown_host", status_code=404)
        await set_current_org(session, org.id)
        stored = await session.scalar(
            select(StoredFile).where(StoredFile.org_id == org.id, StoredFile.id == file_id)
        )
        if stored is None or stored.entity_type not in PUBLIC_ENTITY_TYPES:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return _file_response(stored, request, public=True)
