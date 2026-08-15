"""Gmail endpoints under ``/api/v1/google/gmail`` — all about the **caller's own** mailbox.

What state its feed is in and "scan it now" (#341, :mod:`~app.modules.google.gmail.refresh`),
plus the three that exist because the poller's decisions are not always right: resolve a
reference, read one conversation, and log one named message (#342,
:mod:`~app.modules.google.gmail.manual`). The rules behind each — why the permission is what it
is, why the cooldown lives on a row, why "too soon" is a 200, why the id space is Google's to
guard — live in those two modules rather than here.
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
from app.modules.google.gmail import manual
from app.modules.google.gmail.manual import (
    GmailImportResult,
    GmailLookupResult,
    GmailSearchResult,
)
from app.modules.google.gmail.refresh import (
    GmailRefreshResult,
    GmailSyncStatus,
    gmail_status,
    refresh_my_mailbox,
)

router = APIRouter(prefix="/gmail", tags=["google"])


@router.get(
    "/status",
    response_model=GmailSyncStatus,
    dependencies=[require_permission("google.connection.manage")],
)
async def read_gmail_status(ctx: RequestContext = Depends(require_context)) -> GmailSyncStatus:
    """When this mailbox was last polled, and whether asking for another one is worth it."""
    return await gmail_status(ctx)


@router.post(
    "/refresh",
    response_model=GmailRefreshResult,
    dependencies=[require_permission("google.connection.manage")],
)
async def refresh_gmail(
    ctx: RequestContext = Depends(require_context),
) -> GmailRefreshResult:
    """Poll this mailbox once, now — rate-limited to one manual poll per minute."""
    return await refresh_my_mailbox(ctx)


# --- pulling one message in by hand (#342) ------------------------------------------ #
class GmailImportRequest(BaseModel):
    """One named message, and where it is filed — the ``.eml`` upload's body, minus the file."""

    message_id: str = Field(min_length=1, max_length=128)
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    task_id: uuid.UUID | None = None
    contact_ids: list[uuid.UUID] | None = None
    #: Log it even though a colleague's mailbox already did (the upload's rule, #262).
    allow_duplicate: bool = False
    #: "Laat schakl deze taak invullen" (#327) — available here because the offer hangs off
    #: filing an email onto a task, not off the review transition (#342).
    enrich_task: bool = False


@router.get(
    "/lookup",
    response_model=GmailLookupResult,
    dependencies=[require_permission("google.connection.manage")],
)
async def lookup_gmail_message(
    reference: str = Query(
        ...,
        max_length=2048,
        description="A Gmail link, a message/thread id, or an RFC-822 Message-ID",
    ),
    ctx: RequestContext = Depends(require_context),
) -> GmailLookupResult:
    """Resolve a pasted reference to the message(s) it names, in the caller's own mailbox.

    A **GET**, deliberately: it reads, and a read must survive an expired licence (#307) — the
    module's write gate reads the method, so a POST here would 402 somebody out of looking at
    their own mailbox. It is also why the reference is a query parameter rather than a body.
    """
    return await manual.lookup(ctx, reference)


@router.get(
    "/search",
    response_model=GmailSearchResult,
    dependencies=[require_permission("google.connection.manage")],
)
async def search_gmail(
    participant: str | None = Query(None, max_length=320),
    subject: str | None = Query(None, max_length=200),
    after: date | None = Query(None),
    before: date | None = Query(None),
    ctx: RequestContext = Depends(require_context),
) -> GmailSearchResult:
    """Find a message in the caller's **own** mailbox, by who it was with and when (#372).

    Named parameters rather than one free-text box, and that is a boundary rather than a
    convenience: the service builds the Gmail query from them, so a colon in an address cannot
    become an operator and "what was searched for" stays a sentence we can state.

    ``google.connection.manage`` is the key — the same one every other read of the caller's own
    mailbox declares. This reaches no schakl row at all, so ``interactions.interaction.write``
    is asked for at the point something is actually logged, not here. A **GET** for
    :func:`lookup_gmail_message`'s reason: it reads, and a read must survive an expired
    licence (#307).
    """
    return await manual.search(
        ctx,
        manual.GmailSearchQuery(
            participant=participant, subject=subject, after=after, before=before
        ),
    )


@router.get(
    "/threads/{thread_id}",
    response_model=GmailLookupResult,
    dependencies=[require_permission("google.connection.manage")],
)
async def read_gmail_thread(
    thread_id: str,
    ctx: RequestContext = Depends(require_context),
) -> GmailLookupResult:
    """Every message of one conversation, marked with what is already on the timeline.

    The thread id comes off a row we logged, so this asks about a conversation we were already
    told about — no search, no browsing, and nothing the poller could not already read.
    """
    return await manual.thread_messages(ctx, thread_id)


@router.post(
    "/import",
    response_model=GmailImportResult,
    status_code=201,
    dependencies=[require_permission("interactions.interaction.write")],
)
async def import_gmail_message(
    payload: GmailImportRequest,
    ctx: RequestContext = Depends(require_context),
) -> GmailImportResult:
    """Log one message the poller skipped, filed where the caller says.

    The declared permission is the one for the row this **writes** — a contactmoment — while
    reaching into the mailbox is asked for in the service (``google.connection.manage``). Two
    keys, because it is two acts, and gating on the one the screen happens to be about is how a
    403 becomes unexplainable (#310).
    """
    return await manual.import_message(
        ctx,
        message_id=payload.message_id,
        links={
            "company_id": payload.company_id,
            "project_id": payload.project_id,
            "task_id": payload.task_id,
            **({"contact_ids": payload.contact_ids} if payload.contact_ids is not None else {}),
        },
        enrich_task=payload.enrich_task,
        allow_duplicate=payload.allow_duplicate,
    )
