"""Tenant-scoped file service (issue #123). All DB access via the org-scoped repository.

``write_file`` / ``drop_file`` are module-level on purpose. Three surfaces store bytes for a
person — the generic upload here, a client's logo (``companies``) and an HR dossier document
(``hr``) — and they cannot all go through :class:`FileService`, because each is gated on *its
own* permission rather than on ``files.file.write``. Before de-duplication that cost three
copies of the same six lines; now those lines carry a rule that is silently wrong if one copy
forgets it (**a shared blob's bytes are not one row's to delete**), so there is one copy and
the permission check stays with the caller who owns it.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import BinaryIO

from app.config import settings
from app.core.events import emit
from app.core.scope import entity_visible
from app.core.storage import blobs
from app.core.storage.backend import StorageUnavailableError, get_storage, storage_for
from app.core.storage.models import StoredFile
from app.core.tenancy import RequestContext
from app.errors import AppError

#: Entity types whose files are served **without a session** (`GET /files/{id}/public`):
#: branding assets render on the login screen before anyone is signed in. Uploading one is
#: therefore gated on the branding permission — otherwise any member could publish
#: anonymously-readable files on the org's domain.
PUBLIC_ENTITY_TYPES = frozenset({"branding"})

#: The hosts whose attachments a **client-portal** login reads only when the file says so
#: (``files.client_visible``). These are the records a client may legitimately open — a task
#: ticked visible, their own project, their own company — and the attachments on them are the
#: team's working material by default: a screenshot of a bug, a brief, a scanned contract.
#: Every other entity type keeps its own rule (a report's PDF is gated by ``reporting``, an
#: avatar by nobody), so this list is a closed set and never "everything but".
PORTAL_GATED_ENTITY_TYPES = frozenset({"task", "project", "company"})

logger = logging.getLogger("schakl.storage")


def check_upload(content_type: str, size_bytes: int) -> None:
    """The instance guardrails every stored-file surface applies, in one place."""
    if content_type not in settings.upload_allowed_types:
        raise AppError(
            "validation",
            "errors.upload_type",
            status_code=422,
            fields={"file": "errors.upload_type"},
        )
    if size_bytes > settings.upload_max_bytes:
        raise AppError(
            "validation",
            "errors.upload_too_large",
            status_code=413,
            fields={"file": "errors.upload_too_large"},
        )


async def write_file(
    ctx: RequestContext,
    *,
    filename: str,
    content_type: str,
    stream: BinaryIO,
    entity_type: str | None = None,
    entity_id: uuid.UUID | None = None,
    content_id: str | None = None,
) -> StoredFile:
    """Store a person's upload, de-duplicated, and return its row.

    Authorization belongs to the caller: this is reached from three differently-gated routes
    and enforces none of them. The instance guardrails (allowed type, size ceiling) *are*
    enforced here, against the **measured** size — a multipart ``Content-Length`` is the
    client's claim about its own upload.
    """
    digest, measured = await blobs.digest_stream(stream)
    check_upload(content_type, measured)
    blob = await blobs.reserve(ctx.session, ctx.org.id, sha256=digest, size_bytes=measured)
    if blob.needs_bytes:
        # Blocking IO off the event loop; the row only exists once the bytes do. An S3 put is
        # an external HTTP call, so it must not pin the request's pooled DB connection
        # (docs/PERFORMANCE.md) — release it for the duration; local disk writes are fast and
        # keep the plain path. A de-duplication hit skips this block entirely, which on S3
        # saves the upload round trip as well as the object.
        try:
            if settings.storage_backend == "s3":
                async with ctx.release_db():
                    await asyncio.to_thread(get_storage().put, blob.storage_key, stream)
            else:
                await asyncio.to_thread(get_storage().put, blob.storage_key, stream)
        except Exception:
            # ``release_db`` commits, so the reservation may already be durable: drop it
            # rather than leave a blob the next write de-duplicates onto and finds empty.
            await blobs.release(ctx.session, blob.id)
            raise
    return await ctx.repo(StoredFile).create(
        id=uuid.uuid4(),
        backend=blob.backend,
        storage_key=blob.storage_key,
        blob_id=blob.id,
        filename=filename[:255],
        content_type=content_type,
        size_bytes=measured,
        entity_type=entity_type,
        entity_id=entity_id,
        content_id=content_id,
        # A background job stores files too (a generated report's PDF, #300) and has no user —
        # `SystemContext.user` is None by design. Snapshotting "nobody" is the honest answer,
        # and the column is already nullable for the departed-account case.
        created_by_user_id=getattr(ctx.user, "id", None),
    )


async def drop_file(ctx: RequestContext, stored: StoredFile) -> None:
    """Remove a file row, and its bytes only if this row is the only thing that could own them.

    A **shared** blob's bytes are not this row's to delete: another file may hold the same
    content, and only a whole-table view can tell. So the row goes, the bytes stay, and the
    storage maintenance cron (``jobs.py``) reclaims them once nothing references the blob —
    which also means deleting a file makes no external call on the request path at all.

    A pre-de-duplication row (``blob_id IS NULL``) owns its object outright and keeps the
    original behaviour. Bytes go after the row: a failed row delete keeps the file consistent,
    while a dangling object is merely orphaned space. Deletes dispatch on the row's own backend
    (#190) — a pre-S3 local row keeps deleting from the volume — and an S3 delete, an external
    call, releases the DB connection for the duration. An unreachable backend must not block
    the row's removal: the object is orphaned space, not a broken tenant.
    """
    file_id, backend_name, key = stored.id, stored.backend, stored.storage_key
    shared = stored.blob_id is not None
    await ctx.repo(StoredFile).delete(stored)
    if shared:
        return
    try:
        backend = storage_for(backend_name)
    except StorageUnavailableError:
        logger.warning(
            "deleting file row %s without its bytes: backend=%s is not configured",
            file_id,
            backend_name,
        )
        return
    if backend_name == "s3":
        async with ctx.release_db():
            await asyncio.to_thread(backend.delete, key)
    else:
        await asyncio.to_thread(backend.delete, key)


class FileService:
    def __init__(self, ctx: RequestContext) -> None:
        self.ctx = ctx
        self.repo = ctx.repo(StoredFile)

    async def create(
        self,
        *,
        filename: str,
        content_type: str,
        stream: BinaryIO,
        size_bytes: int,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        content_id: str | None = None,
        client_visible: bool = False,
    ) -> StoredFile:
        # ``content_id`` marks the file as part of the entity's *body* (an inline image a
        # description or comment embeds by marker) rather than an attachment — the e-mail
        # ``cid:`` shape reused, so the strip's list keeps excluding it by construction.
        self.ctx.require("files.file.write")
        if entity_type in PUBLIC_ENTITY_TYPES:
            self.ctx.require("settings.branding.write")
            if not content_type.startswith("image/"):
                raise AppError(
                    "validation",
                    "errors.upload_type",
                    status_code=422,
                    fields={"file": "errors.upload_type"},
                )
        # Cheap refusal on the caller's claimed size before the stream is read at all; the
        # authoritative check is on the measured size, inside ``write_file``.
        check_upload(content_type, size_bytes)
        stored = await write_file(
            self.ctx,
            filename=filename,
            content_type=content_type,
            stream=stream,
            entity_type=entity_type,
            entity_id=entity_id,
            content_id=content_id,
        )
        if client_visible:
            # Set after the write rather than threaded through ``write_file``: the three
            # differently-gated callers of that function (logo, dossier, report) never mean it.
            stored.client_visible = True
            await self.ctx.session.flush()
        # Modules react through the bus (§6): the owning module validates the target exists
        # and writes its own activity line — core storage knows nothing about tasks/projects.
        if entity_type and entity_id:
            await emit("file.attached", self.ctx, self._event_payload(stored, "attached"))
        return stored

    async def delete(self, file_id: uuid.UUID) -> None:
        self.ctx.require("files.file.write")
        stored = await self.get_or_404(file_id)
        if stored.entity_type in PUBLIC_ENTITY_TYPES:
            # Branding assets are published on the login screen; managed by branding managers.
            self.ctx.require("settings.branding.write")
        if stored.entity_type == "avatar" and stored.created_by_user_id != self.ctx.user.id:
            # An avatar is personal: deleting someone else's would break their profile picture.
            raise AppError("forbidden", "errors.forbidden", status_code=403)
        payload = self._event_payload(stored, "removed")
        entity_type, entity_id = stored.entity_type, stored.entity_id
        await drop_file(self.ctx, stored)
        if entity_type and entity_id:
            await emit("file.removed", self.ctx, payload)

    async def set_client_visible(self, file_id: uuid.UUID, visible: bool) -> StoredFile:
        """Flip the client's view of one attachment. Gated like every other write here."""
        self.ctx.require("files.file.write")
        stored = await self.get_or_404(file_id)
        if stored.client_visible != visible:
            stored.client_visible = visible
            await self.ctx.session.flush()
            if stored.entity_type and stored.entity_id:
                await emit(
                    "file.visibility_changed",
                    self.ctx,
                    {**self._event_payload(stored, "visibility"), "client_visible": visible},
                )
        return stored

    def portal_may_read(self, stored: StoredFile) -> bool:
        """The client-portal rule (``ctx.is_portal``, #274): on an attachment host, only a file
        the agency ticked visible; elsewhere the entity's own gate has already answered."""
        if not getattr(self.ctx, "is_portal", False):
            return True
        if stored.entity_type in PORTAL_GATED_ENTITY_TYPES:
            return stored.client_visible
        return True

    async def portal_may_read_serving(self, stored: StoredFile) -> bool:
        """The serve-time answer: :meth:`portal_may_read`, plus the body-content rule.

        A file with a ``content_id`` on an attachment host is not an attachment — it is part
        of the words (an image pasted into a description or a comment), and the words are
        gated by the record's own portal horizon, not by the per-file eye. So it reads
        exactly when the record does (``entity_visible`` applies the model's
        ``__portal_horizon_clause__`` — for a task, ticked visible and inside the client's
        own companies). Serve-time only: the attachment list keeps excluding body files by
        ``content_id`` regardless.
        """
        if self.portal_may_read(stored):
            return True
        if (
            stored.content_id is not None
            and stored.entity_type in PORTAL_GATED_ENTITY_TYPES
            and stored.entity_id is not None
        ):
            return await entity_visible(self.ctx, stored.entity_type, stored.entity_id)
        return False

    async def list_for(
        self, entity_type: str, entity_id: uuid.UUID, *, include_inline: bool = False
    ) -> list[StoredFile]:
        """The entity's **attachments**. A file with a ``content_id`` is part of the entity's
        body, not attached to it — an e-mail's signature logo renders inside the text, and
        listing it here put the same chip on every message that sender ever sent.

        A client-portal login sees only what the agency ticked visible on the attachment hosts
        (``PORTAL_GATED_ENTITY_TYPES``) — the same answer ``serve_file`` gives per id, so a list
        never names a file the fetch would then refuse."""
        rows = await self.repo.list(
            entity_type=entity_type,
            entity_id=entity_id,
            order_by=StoredFile.created_at.asc(),
            limit=200,
        )
        rows = [row for row in rows if self.portal_may_read(row)]
        if include_inline:
            return list(rows)
        return [row for row in rows if row.content_id is None]

    async def get_or_404(self, file_id: uuid.UUID) -> StoredFile:
        # Tenant-scoped repo: a cross-tenant id reads as absent, never as forbidden.
        return await self.repo.get_or_404(file_id)

    def open(self, file: StoredFile) -> BinaryIO:
        # Reads dispatch on the row's backend (#190), never on the instance default.
        return storage_for(file.backend).open(file.storage_key)

    @staticmethod
    def _event_payload(stored: StoredFile, action: str) -> dict:
        return {
            "action": action,
            "file_id": stored.id,
            "entity_type": stored.entity_type,
            "entity_id": stored.entity_id,
            "filename": stored.filename,
            "storage_key": stored.storage_key,
        }
