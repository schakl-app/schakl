"""Gmail endpoints under ``/api/v1/google/gmail`` (#341).

Two routes, both about the **caller's own** mailbox: what state its feed is in, and "scan it
now". The rules behind them — why the permission is ``google.connection.manage``, why the
cooldown lives on the row, why "too soon" is a 200 — are in
:mod:`app.modules.google.gmail.refresh`.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.permissions.deps import require_permission
from app.core.tenancy import RequestContext, require_context
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
