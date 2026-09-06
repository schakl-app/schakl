"""Outlook endpoints under ``/api/v1/microsoft/outlook`` — all about the **caller's own** mailbox.

The Gmail router's twin: what state the feed is in and "scan it now" (#341), plus the reads and
the one write that exist because the poller's decisions are not always right (#342, #372). The
rules — why the permission is what it is, why the cooldown lives on a row, why "too soon" is a
200, why a reference resolves to a conversation, why every read is a **GET** (a read must survive
an expired licence, #307) — are written on the modules the routes delegate to.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.integrations.microsoft.outlook import manual
from app.integrations.microsoft.outlook.manual import (
    OutlookImportResult,
    OutlookLookupResult,
    OutlookSearchResult,
)
from app.integrations.microsoft.outlook.refresh import (
    OutlookRefreshResult,
    OutlookSyncStatus,
    outlook_status,
    refresh_my_mailbox,
)

router = APIRouter(prefix="/outlook", tags=["microsoft"])


@router.get(
    "/status",
    response_model=OutlookSyncStatus,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def read_outlook_status(ctx: RequestContext = Depends(require_context)) -> OutlookSyncStatus:
    """When this mailbox was last polled, and whether asking for another one is worth it."""
    return await outlook_status(ctx)


@router.post(
    "/refresh",
    response_model=OutlookRefreshResult,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def refresh_outlook(ctx: RequestContext = Depends(require_context)) -> OutlookRefreshResult:
    """Poll this mailbox once, now — rate-limited to one manual poll per minute."""
    return await refresh_my_mailbox(ctx)


class OutlookImportRequest(BaseModel):
    """One named message, and where it is filed — the ``.eml`` upload's body, minus the file."""

    message_id: str = Field(min_length=1, max_length=512)
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    task_ids: list[uuid.UUID] | None = None
    contact_ids: list[uuid.UUID] | None = None
    allow_duplicate: bool = False
    enrich_task: bool = False


@router.get(
    "/lookup",
    response_model=OutlookLookupResult,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def lookup_outlook_message(
    reference: str = Query(
        ...,
        max_length=2048,
        description="An Outlook on the web link, a Graph message id, or an RFC-822 Message-ID",
    ),
    ctx: RequestContext = Depends(require_context),
) -> OutlookLookupResult:
    """Resolve a pasted reference to the conversation it names, in the caller's own mailbox."""
    return await manual.lookup(ctx, reference)


@router.get(
    "/search",
    response_model=OutlookSearchResult,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def search_outlook(
    participant: str | None = Query(None, max_length=320),
    subject: str | None = Query(None, max_length=200),
    after: date | None = Query(None),
    before: date | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> OutlookSearchResult:
    """Find a message in the caller's **own** mailbox, by who it was with and when — named
    fields, never raw KQL, so a colon in an address cannot become an operator."""
    return await manual.search(
        ctx,
        manual.OutlookSearchQuery(
            participant=participant, subject=subject, after=after, before=before
        ),
    )


@router.get(
    "/threads/{conversation_id}",
    response_model=OutlookLookupResult,
    dependencies=[require_permission("microsoft.connection.manage")],
)
async def read_outlook_thread(
    conversation_id: str,
    ctx: RequestContext = Depends(require_context),
) -> OutlookLookupResult:
    """Every message of one conversation, marked with what is already on the timeline."""
    return await manual.thread_messages(ctx, conversation_id)


@router.post(
    "/import",
    response_model=OutlookImportResult,
    status_code=201,
    dependencies=[require_permission("interactions.interaction.write")],
)
async def import_outlook_message(
    payload: OutlookImportRequest,
    ctx: RequestContext = Depends(require_context),
) -> OutlookImportResult:
    """Log one message the poller skipped, filed where the caller says. The declared key is the
    one for the row this **writes**; reaching into the mailbox is asked for in the service."""
    return await manual.import_message(
        ctx,
        message_id=payload.message_id,
        links={
            "company_id": payload.company_id,
            "project_id": payload.project_id,
            "task_id": payload.task_id,
            **({"contact_ids": payload.contact_ids} if payload.contact_ids is not None else {}),
            **({"task_ids": payload.task_ids} if payload.task_ids is not None else {}),
        },
        enrich_task=payload.enrich_task,
        allow_duplicate=payload.allow_duplicate,
    )
