"""Tenant-scoped file service (issue #123). All DB access via the org-scoped repository."""

from __future__ import annotations

import asyncio
import uuid
from typing import BinaryIO

from app.config import settings
from app.core.storage.backend import get_storage
from app.core.storage.models import StoredFile
from app.core.tenancy import RequestContext
from app.errors import AppError


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
        return await self.repo.create(
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

    async def get_or_404(self, file_id: uuid.UUID) -> StoredFile:
        # Tenant-scoped repo: a cross-tenant id reads as absent, never as forbidden.
        return await self.repo.get_or_404(file_id)

    def open(self, file: StoredFile) -> BinaryIO:
        return get_storage().open(file.storage_key)
