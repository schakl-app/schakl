"""The manual "scan my mailbox now" half of the Outlook feed — the Gmail button's twin (#341).

The rules are the Gmail refresh's and are written there in full: it is the **caller's own**
mailbox (resolved from ``ctx.user``, never from a parameter — a grant is per-user); the rate
limit is a **row** (``outlook_manual_poll_at``), stamped before the poll and outside its
savepoint so an erroring mailbox still spends its budget; the race is closed by ``SELECT … FOR
UPDATE``; and "too soon" is a 200 carrying ``status="cooldown"``, never an error envelope. The
payload's field names match the Gmail one's exactly, bar the org flag, because the web draws
both feeds' buttons with one component.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.microsoft.models import ConnectionStatus, MicrosoftConnection
from app.integrations.microsoft.oauth import has_mail_scope, microsoft_settings_row
from app.integrations.microsoft.outlook.service import poll_connection

logger = logging.getLogger("schakl.microsoft.outlook")

MANUAL_POLL_COOLDOWN = timedelta(seconds=60)


class OutlookSyncStatus(BaseModel):
    """Everything the button needs to decide whether to draw itself, and what to say."""

    connected: bool = False
    #: The org has the Outlook surface switched on (Instellingen → Microsoft 365).
    outlook_enabled: bool = False
    #: This user opted their own mailbox in (Instellingen → Account).
    sync_enabled: bool = False
    #: Microsoft actually granted the mail scope — an opt-in without it polls nothing.
    scope_granted: bool = False
    connection_error: bool = False
    #: When this mailbox was last polled **by anything** — the cron included.
    last_polled_at: datetime | None = None
    available: bool = False
    #: Seconds until the next manual poll is allowed (0 = now).
    retry_after_seconds: int = 0


class OutlookRefreshResult(BaseModel):
    status: Literal["polled", "cooldown", "error"]
    logged: int = 0
    sync: OutlookSyncStatus


async def _locked_connection(ctx: RequestContext) -> MicrosoftConnection | None:
    return await ctx.session.scalar(
        select(MicrosoftConnection)
        .where(
            MicrosoftConnection.org_id == ctx.org.id,
            MicrosoftConnection.user_id == ctx.user.id,
        )
        .with_for_update()
    )


def _retry_after(connection: MicrosoftConnection, now: datetime) -> int:
    """Whole seconds left on the cooldown — rounded **up**, so 0 always means "go ahead"."""
    stamped = connection.outlook_manual_poll_at
    if stamped is None:
        return 0
    elapsed = now - stamped
    if elapsed >= MANUAL_POLL_COOLDOWN:
        return 0
    remaining = (MANUAL_POLL_COOLDOWN - elapsed).total_seconds()
    return max(1, int(remaining) + (1 if remaining % 1 else 0))


def _status(
    connection: MicrosoftConnection | None, *, outlook_enabled: bool, now: datetime
) -> OutlookSyncStatus:
    if connection is None:
        return OutlookSyncStatus(outlook_enabled=outlook_enabled)
    scope_granted = has_mail_scope(connection.scopes)
    errored = connection.status != ConnectionStatus.ACTIVE.value
    return OutlookSyncStatus(
        connected=True,
        outlook_enabled=outlook_enabled,
        sync_enabled=connection.outlook_sync_enabled,
        scope_granted=scope_granted,
        connection_error=errored,
        last_polled_at=connection.outlook_last_polled_at,
        available=(
            outlook_enabled and connection.outlook_sync_enabled and scope_granted and not errored
        ),
        retry_after_seconds=_retry_after(connection, now),
    )


async def outlook_status(ctx: RequestContext) -> OutlookSyncStatus:
    """Read-only: what the interactions screen renders above its list."""
    row = await microsoft_settings_row(ctx.session, ctx.org.id)
    connection = await ctx.session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == ctx.org.id,
            MicrosoftConnection.user_id == ctx.user.id,
        )
    )
    return _status(
        connection, outlook_enabled=bool(row and row.outlook_enabled), now=datetime.now(UTC)
    )


async def refresh_my_mailbox(ctx: RequestContext) -> OutlookRefreshResult:
    """Poll the caller's own mailbox once, within the cooldown."""
    row = await microsoft_settings_row(ctx.session, ctx.org.id)
    outlook_enabled = bool(row and row.outlook_enabled)
    if not outlook_enabled:
        raise AppError("outlook_disabled", "errors.outlook_disabled", status_code=409)

    connection = await _locked_connection(ctx)
    if connection is None:
        raise AppError("microsoft_not_connected", "errors.microsoft_not_connected", status_code=409)
    if not connection.outlook_sync_enabled or not has_mail_scope(connection.scopes):
        raise AppError("outlook_sync_off", "errors.outlook_sync_off", status_code=409)
    if connection.status != ConnectionStatus.ACTIVE.value:
        raise AppError(
            "microsoft_connection_error", "errors.microsoft_connection_error", status_code=409
        )

    now = datetime.now(UTC)
    if _retry_after(connection, now) > 0:
        return OutlookRefreshResult(
            status="cooldown",
            sync=_status(connection, outlook_enabled=outlook_enabled, now=now),
        )

    # Stamped before the poll and *outside* its savepoint: a mailbox that raises must still
    # spend its budget, or an erroring grant can be hammered a click at a time.
    connection.outlook_manual_poll_at = now
    await ctx.session.flush()

    logged = 0
    status: Literal["polled", "error"] = "polled"
    try:
        async with ctx.session.begin_nested():
            logged = await poll_connection(ctx.session, ctx.org, connection)
    except Exception:  # noqa: BLE001 — reported to the caller, never a 500 on a refresh button
        logger.exception(
            "Manual Outlook refresh failed for connection %s (org %s)", connection.id, ctx.org.id
        )
        status = "error"

    return OutlookRefreshResult(
        status=status,
        logged=logged,
        sync=_status(connection, outlook_enabled=outlook_enabled, now=datetime.now(UTC)),
    )


__all__ = [
    "MANUAL_POLL_COOLDOWN",
    "OutlookRefreshResult",
    "OutlookSyncStatus",
    "outlook_status",
    "refresh_my_mailbox",
]
