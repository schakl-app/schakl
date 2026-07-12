"""REST surface for stored files (issue #123): upload + guarded serving.

Files are served **through the API, never raw-static**, so tenant scoping and access control
apply — a task attachment can be sensitive. `GET` streams with an ETag (the immutable file id)
and honours `If-None-Match`, so a repeat avatar fetch costs a 304.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request, UploadFile
from fastapi.responses import Response, StreamingResponse

from app.core.permissions.deps import no_permission_required, require_permission
from app.core.storage.schemas import StoredFileRead
from app.core.storage.service import FileService
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError

router = APIRouter(prefix="/files", tags=["files"])

#: Types a browser may render inline; anything else downloads. SVG is deliberately NOT inline —
#: an inline SVG executes script in the serving origin, which would be a stored-XSS hole.
_INLINE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"}
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
    service = FileService(ctx)
    stored = await service.get_or_404(file_id)
    etag = f'"{stored.id}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    disposition = "inline" if stored.content_type in _INLINE_TYPES else "attachment"
    try:
        stream = service.open(stored)
    except FileNotFoundError:
        # A row without bytes: the backend lost them (restored DB without the volume).
        raise AppError("not_found", "errors.not_found", status_code=404) from None
    filename = stored.filename.replace('"', "")
    return StreamingResponse(
        stream,
        media_type=stored.content_type,
        headers={
            "ETag": etag,
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'{disposition}; filename="{filename}"',
            "Content-Length": str(stored.size_bytes),
        },
    )
