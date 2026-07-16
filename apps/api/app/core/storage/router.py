"""REST surface for stored files (issue #123): upload + guarded serving.

Files are served **through the API, never raw-static**, so tenant scoping and access control
apply — a task attachment can be sensitive. `GET` streams with an ETag (the immutable file id)
and honours `If-None-Match`, so a repeat avatar fetch costs a 304.

Branding assets (logo/favicon) are the one anonymous exception: they render on the login
screen before a session exists, so `GET /files/{id}/public` serves them with the org resolved
from the hostname alone — and reaches *only* rows tagged with a public entity type.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
import uuid

from fastapi import APIRouter, Depends, Query, Request, UploadFile
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

logger = logging.getLogger("schakl.storage")

router = APIRouter(prefix="/files", tags=["files"])

#: Types a browser may render inline; anything else downloads. SVG is deliberately NOT inline —
#: an inline SVG executes script in the serving origin, which would be a stored-XSS hole.
_INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"})


def _file_response(stored: StoredFile, request: Request, *, public: bool = False) -> Response:
    """Stream a stored file with ETag/304, honouring the inline allow-list."""
    etag = f'"{stored.id}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    disposition = "inline" if stored.content_type in _INLINE_TYPES else "attachment"
    try:
        stream = get_storage().open(stored.storage_key)
    except FileNotFoundError:
        # The row exists but its bytes are gone — the DB and the file store have drifted apart.
        # On a standard single-host deploy api + worker share the storage volume, so this is a
        # misconfiguration, not a bad link (#180): the worker that saved the attachment wrote to a
        # different filesystem than the API is serving from (e.g. api run outside Docker while the
        # worker ran in it), or the storage volume was recreated while the DB persisted. Log it
        # loudly and distinctly — a generic 404 read as "bad id" and hid a fixable ops problem.
        logger.warning(
            "stored file %s has no bytes at backend=%s key=%s (entity=%s/%s) — storage volume "
            "not co-located with the writer, or lost; the DB row is intact",
            stored.id,
            stored.backend,
            stored.storage_key,
            stored.entity_type,
            stored.entity_id,
        )
        raise AppError("not_found", "errors.file_bytes_missing", status_code=404) from None
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
    "",
    response_model=list[StoredFileRead],
    dependencies=[
        no_permission_required(
            "any signed-in member may list their tenant's files (they can already fetch each "
            "one unpermissioned); rows are RLS-scoped and filtered to one entity"
        )
    ],
)
async def list_files(
    entity_type: str,
    entity_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> list[StoredFileRead]:
    """The files attached to one entity (a task's documents, a project's documents)."""
    rows = await FileService(ctx).list_for(entity_type, entity_id)
    return [StoredFileRead.model_validate(row) for row in rows]


@router.delete(
    "/{file_id}",
    status_code=204,
    dependencies=[require_permission("files.file.write")],
)
async def delete_file(
    file_id: uuid.UUID,
    ctx: RequestContext = Depends(require_context),
) -> None:
    """Remove the row and its bytes. Branding/avatar files carry extra guards (service)."""
    await FileService(ctx).delete(file_id)


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


# The size variants the installable-app icon story needs (#198): apple-touch (180) and the
# manifest's 192/512 (purpose any + maskable). A closed set so this can't become a generic
# image-resizing proxy.
_ICON_SIZES = frozenset({180, 192, 512})
_HEX_BG = re.compile(r"^#[0-9a-fA-F]{6}$")


def _iconify(data: bytes, size: int, maskable: bool, bg: str) -> bytes:
    """Square-crop and resize an image to a PNG app icon; the maskable variant keeps the
    artwork inside the ~80% safe zone on an opaque background, so a round Android mask never
    clips it. Pillow work — callers run this in a thread."""
    from PIL import Image  # local import: Pillow loads only when an icon variant is asked for

    with Image.open(io.BytesIO(data)) as source:
        img = source.convert("RGBA")
        side = min(img.size)
        left = (img.width - side) // 2
        top = (img.height - side) // 2
        img = img.crop((left, top, left + side, top + side))
        if maskable:
            inner = round(size * 0.8)
            icon = img.resize((inner, inner), Image.LANCZOS)
            canvas = Image.new("RGBA", (size, size), bg)
            canvas.paste(icon, ((size - inner) // 2, (size - inner) // 2), icon)
            img = canvas
        else:
            img = img.resize((size, size), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, "PNG")
        return out.getvalue()


@router.get(
    "/{file_id}/public",
    dependencies=[
        no_permission_required(
            "branding assets (logo/favicon) render on the login screen before a session "
            "exists; only rows tagged with a public entity type are reachable here"
        )
    ],
)
async def serve_public_file(
    file_id: uuid.UUID,
    request: Request,
    size: int | None = Query(default=None),
    maskable: bool = Query(default=False),
    bg: str = Query(default="#ffffff", max_length=7),
) -> Response:
    """Anonymous serving for branding assets, org resolved strictly from the hostname.

    Suspended orgs still resolve (their login screen keeps its branding, matching
    `/meta/tenant`); deleted orgs — and any unknown host — read as 404.

    ``size`` (180/192/512, #198) answers a resized square PNG for the PWA manifest and the
    apple-touch-icon; ``maskable`` pads the artwork into the safe zone on the ``bg`` colour.
    Only raster images resize — an SVG (or a decode failure) falls back to the original bytes,
    a degraded icon rather than a broken install.
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
        if (
            size in _ICON_SIZES
            and stored.content_type.startswith("image/")
            and stored.content_type != "image/svg+xml"
        ):
            background = bg if _HEX_BG.match(bg) else "#ffffff"
            etag = f'"{stored.id}-{size}{"m" if maskable else ""}-{background[1:]}"'
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers={"ETag": etag})
            try:
                raw = await asyncio.to_thread(
                    lambda: get_storage().open(stored.storage_key).read()
                )
            except FileNotFoundError:
                raise AppError(
                    "not_found", "errors.file_bytes_missing", status_code=404
                ) from None
            try:
                png = await asyncio.to_thread(_iconify, raw, size, maskable, background)
            except Exception:  # noqa: BLE001 — a bad image degrades, never 500s an icon fetch
                logger.warning("app icon %s could not be resized; serving original", stored.id)
            else:
                return Response(
                    png,
                    media_type="image/png",
                    headers={
                        "ETag": etag,
                        "Cache-Control": "public, max-age=3600",
                        "X-Content-Type-Options": "nosniff",
                        "Content-Disposition": 'inline; filename="icon.png"',
                    },
                )
        return _file_response(stored, request, public=True)
