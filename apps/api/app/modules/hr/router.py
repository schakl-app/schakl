"""REST surface for the employee dossier under ``/api/v1/hr`` (§6, §9).

Serving is deliberately **not** the generic ``/files/{id}`` route: a contract copy is about
as sensitive as tenant data gets, so the bytes travel a route that re-checks the dossier
permission (own vs any) on every fetch — and the generic route refuses ``hr_document`` rows
outside the caller's own dossier (see ``app/core/storage/router.py``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.core.activity import ActivityService
from app.core.auth.models import User
from app.core.permissions.deps import require_permission
from app.core.storage.models import StoredFile
from app.core.storage.router import _file_response
from app.core.storage.service import drop_file, write_file
from app.core.tenancy import RequestContext, require_context
from app.errors import AppError
from app.modules.hr.models import DOCUMENT_CATEGORIES, HrDocument

router = APIRouter(prefix="/hr", tags=["hr"])

_ENTITY_TYPE = "hr_document"


class HrDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    category: str
    title: str
    file_id: uuid.UUID
    note: str | None = None
    uploaded_by_name: str | None = None
    created_at: datetime | None = None


class DossierRead(BaseModel):
    user_id: uuid.UUID
    documents: list[HrDocumentRead]
    categories: list[str] = list(DOCUMENT_CATEGORIES)


def _target_user_id(ctx: RequestContext, user_id: uuid.UUID | None) -> uuid.UUID:
    """Own dossier by default; someone else's needs the ``:any`` scope — with a 404 answer
    for the id itself left to the queries (nothing here leaks whether a user exists)."""
    if user_id is None or user_id == ctx.user.id:
        return ctx.user.id
    ctx.require("hr.dossier.read", scope="any")
    return user_id


@router.get(
    "/dossier",
    response_model=DossierRead,
    dependencies=[require_permission("hr.dossier.read")],
)
async def dossier(
    user_id: uuid.UUID | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> DossierRead:
    target = _target_user_id(ctx, user_id)
    rows = await ctx.repo(HrDocument).list(
        user_id=target, order_by=HrDocument.created_at.desc(), limit=500
    )
    return DossierRead(
        user_id=target, documents=[HrDocumentRead.model_validate(row) for row in rows]
    )


@router.post(
    "/documents",
    response_model=HrDocumentRead,
    status_code=201,
    dependencies=[require_permission("hr.document.manage")],
)
async def upload_document(
    file: UploadFile,
    user_id: uuid.UUID,
    category: str,
    title: str = "",
    note: str = "",
    ctx: RequestContext = Depends(require_context),
) -> HrDocumentRead:
    if category not in DOCUMENT_CATEGORIES:
        raise AppError(
            "validation",
            "errors.validation",
            status_code=422,
            fields={"category": "errors.validation"},
        )
    member = await ctx.session.scalar(select(User.id).where(User.id == user_id))
    if member is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    # Type allow-list, size ceiling and the de-duplicated write live in the storage core; the
    # *permission* is this route's own (`hr.document.manage`), which is why this is not
    # `FileService`.
    stored = await write_file(
        ctx,
        filename=file.filename or "document",
        content_type=file.content_type or "application/octet-stream",
        stream=file.file,
        entity_type=_ENTITY_TYPE,
        entity_id=user_id,
    )
    actor = ctx.user.full_name or ctx.user.email
    row = await ctx.repo(HrDocument).create(
        user_id=user_id,
        category=category,
        title=(title or stored.filename)[:255],
        file_id=stored.id,
        note=note[:500] or None,
        uploaded_by_name=actor,
    )
    await ActivityService(ctx).record(
        "hr_dossier", user_id, "document_added", {"category": category, "title": row.title}
    )
    return HrDocumentRead.model_validate(row)


@router.delete(
    "/documents/{document_id}",
    status_code=204,
    dependencies=[require_permission("hr.document.manage")],
)
async def delete_document(
    document_id: uuid.UUID, ctx: RequestContext = Depends(require_context)
) -> None:
    row = await ctx.repo(HrDocument).get_or_404(document_id)
    stored = await ctx.repo(StoredFile).get(row.file_id)
    payload = {"category": row.category, "title": row.title}
    target = row.user_id
    await ctx.repo(HrDocument).delete(row)
    if stored is not None:
        # Never delete the object by key here: two dossier entries may be the same signed PDF.
        await drop_file(ctx, stored)
    await ActivityService(ctx).record("hr_dossier", target, "document_removed", payload)


@router.get(
    "/documents/{document_id}/file",
    dependencies=[require_permission("hr.dossier.read")],
)
async def serve_document(
    document_id: uuid.UUID,
    request: Request,
    ctx: RequestContext = Depends(require_context),
):
    row = await ctx.repo(HrDocument).get_or_404(document_id)
    if row.user_id != ctx.user.id:
        # Not yours: reading someone else's dossier takes the :any scope — as a 404, so the
        # document id doesn't confirm a colleague's dossier entry exists.
        if not ctx.can("hr.dossier.read", scope="any"):
            raise AppError("not_found", "errors.not_found", status_code=404)
    stored = await ctx.repo(StoredFile).get_or_404(row.file_id)
    return await _file_response(stored, request, ctx=ctx)
