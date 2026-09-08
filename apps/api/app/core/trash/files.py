"""The one dependent every trashable entity has: the documents pinned to it (docs/STORAGE.md).

A file row hangs off ``(entity_type, entity_id)`` with no FK, so a purge that only deleted the
record would leave its documents as orphan rows pointing at nothing — readable by nobody and
reclaimed by nothing. Core owns both tables, so core contributes this dependent to every spec
rather than asking each module to remember it. It goes with the record (a document is filed
*under* a client; it is not a record of its own the way an invoice is), and the purge runs each
row through ``drop_file`` so shared bytes stay shared and owned bytes are actually reclaimed.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import bindparam, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.storage.models import StoredFile
from app.core.storage.service import drop_file
from app.core.tenancy import RequestContext
from app.core.trash.spec import TrashDependent

_COUNT = text(
    "SELECT entity_id AS anchor, count(*) AS n FROM files "
    "WHERE org_id = :oid AND entity_type = :et AND entity_id IN :ids GROUP BY entity_id"
).bindparams(bindparam("ids", expanding=True))


def files_dependent(entity_type: str) -> TrashDependent:
    async def count(
        session: AsyncSession, org_id: uuid.UUID, ids: Sequence[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not ids:
            return {}
        rows = await session.execute(_COUNT, {"oid": org_id, "et": entity_type, "ids": list(ids)})
        return {row.anchor: int(row.n) for row in rows}

    async def purge(ctx: RequestContext, entity_id: uuid.UUID) -> None:
        repo = ctx.repo(StoredFile)
        stored = (
            await ctx.session.execute(
                repo.scoped_select().where(
                    StoredFile.entity_type == entity_type, StoredFile.entity_id == entity_id
                )
            )
        ).scalars()
        for row in list(stored):
            await drop_file(ctx, row)

    return TrashDependent(
        key="files.documents",
        label_key="trash.dependent.files.documents",
        blocks=False,
        count=count,
        purge=purge,
    )
