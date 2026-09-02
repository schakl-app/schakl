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
import base64
import binascii
import io
import logging
import re
import uuid

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import select

from app.config import settings
from app.core.permissions.deps import no_permission_required, require_permission
from app.core.scope import entity_visible
from app.core.storage.backend import StorageUnavailableError, storage_for
from app.core.storage.models import StoredFile
from app.core.storage.schemas import InlineUpload, StoredFileRead, StoredFileUpdate
from app.core.storage.service import PUBLIC_ENTITY_TYPES, FileService, check_upload
from app.core.tenancy import RequestContext, request_hostname, require_context, resolve_org
from app.db import async_session_maker, set_current_org
from app.errors import AppError

logger = logging.getLogger("schakl.storage")

router = APIRouter(prefix="/files", tags=["files"])

#: Types a browser may render inline; anything else downloads. SVG is deliberately NOT inline —
#: an inline SVG executes script in the serving origin, which would be a stored-XSS hole.
_INLINE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif", "application/pdf"})


async def _open_stored(stored: StoredFile, ctx: RequestContext | None = None):
    """Resolve the row's own backend (#190) and open its bytes off the event loop.

    An S3 read is an external HTTP call: with a ``ctx`` it runs inside ``release_db()`` so it
    never pins the request's pooled DB connection (docs/PERFORMANCE.md) — safe because
    ``S3ObjectStorage.open`` buffers fully, so no S3 socket outlives the call either.
    """
    try:
        backend = storage_for(stored.backend)
    except StorageUnavailableError:
        # The row was written by a backend this instance can no longer reach (e.g. an `s3`
        # row after the S3 env config was removed). Distinct from bytes-missing: the ops fix
        # is to restore SCHAKL_STORAGE_S3_*, not to hunt a volume.
        logger.warning(
            "stored file %s needs backend=%s which is not configured on this instance",
            stored.id,
            stored.backend,
        )
        raise AppError(
            "not_found", "errors.storage_backend_unavailable", status_code=404
        ) from None
    try:
        if stored.backend == "s3" and ctx is not None:
            async with ctx.release_db():
                return await asyncio.to_thread(backend.open, stored.storage_key)
        return await asyncio.to_thread(backend.open, stored.storage_key)
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


async def _file_response(
    stored: StoredFile,
    request: Request,
    *,
    public: bool = False,
    ctx: RequestContext | None = None,
) -> Response:
    """Stream a stored file with ETag/304, honouring the inline allow-list."""
    etag = f'"{stored.id}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    disposition = "inline" if stored.content_type in _INLINE_TYPES else "attachment"
    stream = await _open_stored(stored, ctx)
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
    inline: bool = False,
    ctx: RequestContext = Depends(require_context),
) -> StoredFileRead:
    """Multipart upload. Size and content type are bounded by instance config.

    ``inline=true`` stores the file as part of its entity's **body** rather than as an
    attachment (``content_id``, the e-mail ``cid:`` shape): an image pasted into a task's
    description or a comment renders inside the text via its ``![alt](file:<id>)`` marker,
    so it must not also appear in the attachment strip.
    """
    # You cannot attach a document to a record you cannot see (#285) — the same rule the
    # repository applies to ``company_id`` on an ordinary write, for the entity-reference pair.
    if entity_type and entity_id and not await entity_visible(ctx, entity_type, entity_id):
        raise AppError("not_found", "errors.not_found", status_code=404)
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
        content_id="body" if inline else None,
    )
    return StoredFileRead.model_validate(stored)


@router.post(
    "/inline",
    response_model=StoredFileRead,
    status_code=201,
    dependencies=[require_permission("files.file.write")],
)
async def upload_file_inline(
    body: InlineUpload,
    ctx: RequestContext = Depends(require_context),
) -> StoredFileRead:
    """The same upload as ``POST /files``, carried as base64 inside a JSON body.

    This is the route an MCP tool, an n8n node or any JSON-only automation can actually call
    (docs/MCP.md): a generated tool sends a JSON document, and a ``multipart/form-data`` route
    answers that with ``422 file: field required`` however the bytes were meant. Same
    guardrails, same de-duplication, same activity line — only the envelope differs.
    """
    if body.entity_type and body.entity_id and not await entity_visible(
        ctx, body.entity_type, body.entity_id
    ):
        raise AppError("not_found", "errors.not_found", status_code=404)
    payload = body.data
    # A ``data:image/png;base64,....`` URL is what a browser hands you for a pasted image and
    # what a model tends to write; the prefix carries nothing the body does not already state.
    if payload.startswith("data:"):
        _, _, payload = payload.partition(",")
    # Refuse by the *encoded* length before decoding: base64 is 4/3 of the bytes, so a body
    # that cannot possibly fit under the ceiling never costs the decode — the same "check the
    # cap before the work it bounds" rule the import parser follows (§17).
    if len(payload) > settings.upload_max_bytes * 4 // 3 + 4:
        raise AppError(
            "validation",
            "errors.upload_too_large",
            status_code=413,
            fields={"data": "errors.upload_too_large"},
        )
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise AppError(
            "validation",
            "errors.invalid_base64",
            status_code=422,
            fields={"data": "errors.invalid_base64"},
        ) from None
    check_upload(body.content_type, len(raw))
    stored = await FileService(ctx).create(
        filename=body.filename,
        content_type=body.content_type,
        stream=io.BytesIO(raw),
        size_bytes=len(raw),
        entity_type=body.entity_type,
        entity_id=body.entity_id,
        content_id="body" if body.inline else None,
        client_visible=body.client_visible,
    )
    return StoredFileRead.model_validate(stored)


@router.patch(
    "/{file_id}",
    response_model=StoredFileRead,
    dependencies=[require_permission("files.file.write")],
)
async def update_file(
    file_id: uuid.UUID,
    body: StoredFileUpdate,
    ctx: RequestContext = Depends(require_context),
) -> StoredFileRead:
    """Tick or untick "the client may see this" on one attachment (the model's
    ``client_visible``). The only editable fact about a stored file: its bytes are immutable
    by construction (content-addressed) and its name is what the uploader gave it."""
    stored = await FileService(ctx).set_client_visible(file_id, body.client_visible)
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
    include_inline: bool = False,
    ctx: RequestContext = Depends(require_context),
) -> list[StoredFileRead]:
    """The files attached to one entity (a task's documents, a project's documents).

    ``include_inline`` also returns the files that are part of the entity's *body* — an
    e-mail's ``cid:`` images. Off by default: they render inside the text, and listing them
    beside the attachments put the sender's signature logo on every message.
    """
    if entity_type == "company_logo" and ctx.company_scope is not None:
        # Same horizon rule as serving one (#191/#196).
        if entity_id not in ctx.company_scope:
            return []
    if entity_type == "hr_document":
        # Dossier documents list only for their owner or a dossier manager.
        if entity_id != ctx.user.id and not ctx.can("hr.dossier.read", scope="any"):
            return []
    # Every *other* entity type went unchecked, so a membership restricted to a company group
    # could list the documents attached to a client, task or project it cannot otherwise see
    # (#285). Ask the record's own model through the tenant-scoped repository — the company_logo
    # special case above predates the registry and stays as the cheaper direct answer.
    if not await entity_visible(ctx, entity_type, entity_id):
        return []
    rows = await FileService(ctx).list_for(
        entity_type, entity_id, include_inline=include_inline
    )
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
    service = FileService(ctx)
    stored = await service.get_or_404(file_id)
    _company_horizon_guard(ctx, stored)
    await _portal_guard(service, stored)
    return await _file_response(stored, request, ctx=ctx)


async def _portal_guard(service: FileService, stored: StoredFile) -> None:
    """A client-portal login reads an attachment on a task, project or company only when the
    agency ticked it visible — 404, the same answer the list gives by leaving it out. A file
    that is part of the entity's *body* (``content_id`` — an image pasted into a description
    or a comment) follows the text that embeds it instead: readable exactly when the record
    is, because the eye never governed what the words already show."""
    if not await service.portal_may_read_serving(stored):
        raise AppError("not_found", "errors.not_found", status_code=404)


#: Thumbnail long-edge sizes (px): a chip in an attachment strip, a card preview, a lightbox
#: that still fits a laptop screen. A closed set, like ``_ICON_SIZES`` — this is a preview of
#: a stored image, not a general-purpose resizing proxy.
_THUMB_SIZES = frozenset({160, 480, 1200})
#: Formats Pillow decodes and a browser draws inline; an SVG never (script), a PDF never (no
#: raster to scale — the first page of a PDF is a different feature).
_THUMB_SOURCE_TYPES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})


def _thumbnail(data: bytes, size: int) -> tuple[bytes, str]:
    """Scale an image down so its long edge is ``size`` px, keeping the aspect ratio.

    A source smaller than that is re-encoded at its own size rather than blown up. Alpha stays
    alpha (PNG); an opaque source comes back as JPEG, which is what makes a 4 MB screenshot a
    30 kB chip. Animated GIFs lose their animation — a thumbnail is a still. Pillow work, so
    callers run this in a thread; Pillow's own decompression-bomb ceiling stays in force.
    """
    from PIL import Image, ImageOps  # local import: Pillow loads only when a preview is asked for

    with Image.open(io.BytesIO(data)) as source:
        # Honour the EXIF orientation a phone camera writes, or every portrait photo lies down.
        img = ImageOps.exif_transpose(source) or source
        has_alpha = img.mode in ("RGBA", "LA") or (
            img.mode == "P" and "transparency" in img.info
        )
        img = img.convert("RGBA" if has_alpha else "RGB")
        img.thumbnail((size, size), Image.LANCZOS)
        out = io.BytesIO()
        if has_alpha:
            img.save(out, "PNG", optimize=True)
            return out.getvalue(), "image/png"
        img.save(out, "JPEG", quality=82, optimize=True, progressive=True)
        return out.getvalue(), "image/jpeg"


@router.get(
    "/{file_id}/thumbnail",
    dependencies=[
        no_permission_required(
            "same gate as fetching the file itself: any signed-in member, RLS-scoped row, "
            "company horizon and portal visibility applied exactly as GET /files/{id}"
        )
    ],
)
async def serve_thumbnail(
    file_id: uuid.UUID,
    request: Request,
    size: int = Query(default=480),
    ctx: RequestContext = Depends(require_context),
) -> Response:
    """A scaled-down preview of a stored raster image, so an attachment strip shows the
    screenshot rather than its filename and a client card shows the logo proof rather than a
    paperclip. Computed on demand, cached by ETag: the same 304 economy as the original.

    Only ``_THUMB_SIZES`` are served (anything else snaps to the nearest), only raster images
    are scaled, and a file that is not one — or that Pillow cannot decode — answers the
    original bytes, so an ``<img>`` still draws *something* rather than a broken icon.
    """
    service = FileService(ctx)
    stored = await service.get_or_404(file_id)
    _company_horizon_guard(ctx, stored)
    await _portal_guard(service, stored)
    if stored.content_type not in _THUMB_SOURCE_TYPES:
        return await _file_response(stored, request, ctx=ctx)
    size = min(_THUMB_SIZES, key=lambda candidate: abs(candidate - size))
    etag = f'"{stored.id}-t{size}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag})
    raw = await asyncio.to_thread((await _open_stored(stored, ctx)).read)
    try:
        body, media_type = await asyncio.to_thread(_thumbnail, raw, size)
    except Exception:  # noqa: BLE001 — a bad image degrades to the original, never a 500
        logger.warning("thumbnail for %s could not be rendered; serving original", stored.id)
        return await _file_response(stored, request, ctx=ctx)
    return Response(
        body,
        media_type=media_type,
        headers={
            "ETag": etag,
            "Cache-Control": "private, max-age=3600",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": "inline",
        },
    )


def _company_horizon_guard(ctx: RequestContext, stored: StoredFile) -> None:
    """A company-rooted file honours the caller's company horizon (#191/#196): a portal
    login (or a restricted member) must not fetch an invisible company's logo by file id.
    Same answer the company itself gives — 404, never a leaking 403."""
    if (
        stored.entity_type == "company_logo"
        and ctx.company_scope is not None
        and stored.entity_id not in ctx.company_scope
    ):
        raise AppError("not_found", "errors.not_found", status_code=404)
    # An HR dossier document (a contract copy) is the most sensitive blob in the tenant:
    # only its owner or a dossier manager reads it — everyone else gets the same 404 the
    # dossier route answers, whatever route the file id arrived through.
    if stored.entity_type == "hr_document":
        if stored.entity_id != ctx.user.id and not ctx.can("hr.dossier.read", scope="any"):
            raise AppError("not_found", "errors.not_found", status_code=404)


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
    # Load the row inside its own short session, fetch the bytes *after* the block closes —
    # so the anonymous path never holds a DB connection across storage IO either (#190).
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
        raw = await asyncio.to_thread((await _open_stored(stored)).read)
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
    return await _file_response(stored, request, public=True)
