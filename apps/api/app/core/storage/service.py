"""Tenant-scoped file service (issue #123). All DB access via the org-scoped repository."""

from __future__ import annotations

import asyncio
import uuid
from typing import BinaryIO

from app.config import settings
from app.core.events import emit
from app.core.storage.backend import get_storage
from app.core.storage.models import StoredFile
from app.core.tenancy import RequestContext
from app.errors import AppError

#: Entity types whose files are served **without a session** (`GET /files/{id}/public`):
#: branding assets render on the login screen before anyone is signed in. Uploading one is
#: therefore gated on the branding permission — otherwise any member could publish
#: anonymously-readable files on the org's domain.
PUBLIC_ENTITY_TYPES = frozenset({"branding"})


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
    ) -> StoredFile:
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
        file_id = uuid.uuid4()
        key = f"{self.ctx.org.id}/{file_id}"
        # Blocking filesystem IO off the event loop; the row only exists once the bytes do.
        await asyncio.to_thread(get_storage().put, key, stream)
        stored = await self.repo.create(
            id=file_id,
            backend=settings.storage_backend,
            storage_key=key,
            filename=filename[:255],
            content_type=content_type,
            size_bytes=size_bytes,
            entity_type=entity_type,
            entity_id=entity_id,
            created_by_user_id=self.ctx.user.id,
        )
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
        await self.repo.delete(stored)
        # Bytes go after the row: a failed row delete keeps the file consistent, while a
        # dangling blob is merely orphaned space.
        await asyncio.to_thread(get_storage().delete, payload["storage_key"])
        if entity_type and entity_id:
            await emit("file.removed", self.ctx, payload)

    async def list_for(self, entity_type: str, entity_id: uuid.UUID) -> list[StoredFile]:
        rows = await self.repo.list(
            entity_type=entity_type,
            entity_id=entity_id,
            order_by=StoredFile.created_at.asc(),
            limit=200,
        )
        return list(rows)

    async def get_or_404(self, file_id: uuid.UUID) -> StoredFile:
        # Tenant-scoped repo: a cross-tenant id reads as absent, never as forbidden.
        return await self.repo.get_or_404(file_id)

    def open(self, file: StoredFile) -> BinaryIO:
        return get_storage().open(file.storage_key)

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
