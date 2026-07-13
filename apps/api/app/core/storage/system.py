"""System-actor file writes — the published surface background jobs store bytes through.

The gmail worker saves an approved email's attachments (#180) with a ``SystemContext`` —
no person, so ``FileService``'s permission checks don't apply; authorization happened when
the mailbox owner connected Gmail and approved the message. Validation (allowed types, the
size ceiling) is the same as an upload: a skipped attachment returns ``None`` rather than
failing the caller, because one oversized PDF must not lose the message body.
"""

from __future__ import annotations

import asyncio
import io
import uuid

from sqlalchemy import func, select

from app.config import settings
from app.core.events import EmitContext
from app.core.storage.backend import get_storage
from app.core.storage.models import StoredFile


async def entity_has_files(ctx: EmitContext, entity_type: str, entity_id: uuid.UUID) -> bool:
    """Idempotency probe: a re-run job must not store the same attachments twice."""
    count = await ctx.session.scalar(
        select(func.count())
        .select_from(StoredFile)
        .where(
            StoredFile.org_id == ctx.org.id,
            StoredFile.entity_type == entity_type,
            StoredFile.entity_id == entity_id,
        )
    )
    return int(count or 0) > 0


async def store_system_file(
    ctx: EmitContext,
    *,
    filename: str,
    content_type: str,
    data: bytes,
    entity_type: str,
    entity_id: uuid.UUID,
    created_by_user_id: uuid.UUID | None = None,
) -> StoredFile | None:
    """Store one file for a background actor; ``None`` when validation skips it."""
    if content_type not in settings.upload_allowed_types:
        return None
    if len(data) > settings.upload_max_bytes:
        return None
    file_id = uuid.uuid4()
    key = f"{ctx.org.id}/{file_id}"
    # Blocking filesystem IO off the event loop; the row only exists once the bytes do.
    await asyncio.to_thread(get_storage().put, key, io.BytesIO(data))
    row = StoredFile(
        id=file_id,
        org_id=ctx.org.id,
        backend=settings.storage_backend,
        storage_key=key,
        filename=filename[:255],
        content_type=content_type,
        size_bytes=len(data),
        entity_type=entity_type,
        entity_id=entity_id,
        created_by_user_id=created_by_user_id,
    )
    ctx.session.add(row)
    await ctx.session.flush()
    return row
