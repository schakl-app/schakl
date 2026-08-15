"""The manual "scan my mailbox now" half of the Gmail feed (#341).

The cron polls every mailbox every five minutes (``google_gmail_poll``), which is right for a
background feed and wrong for the moment somebody has *just* sent the email they want on the
client's timeline. This is that moment: one poll, of the caller's **own** mailbox, on request.

Three things are worth stating, because each is a rule and not an implementation detail.

**It is the caller's own mailbox, never a colleague's.** A Gmail grant is per-user and opt-in
(docs/GOOGLE.md §6) and raw tokens never leave :mod:`app.modules.google.client`; "refresh
everyone's mail" would be one person spending everyone else's quota against grants they did
not make. So the route asks for ``google.connection.manage`` — the same key as the rest of
*your own* connection — and resolves the connection from ``ctx.user``, never from a parameter.

**The rate limit is a row, not a Redis bucket.** What we are protecting is Google's per-user
quota, and the thing that knows when this mailbox was last asked is the connection row. Keeping
it there means the ceiling survives a Redis outage, holds across both API replicas, and is the
same value the button already has to render ("laatst ververst"). It is stamped **before** the
poll runs and outside its savepoint, so a Gmail error cannot hand the caller an unlimited retry
loop against a mailbox that is already failing — the failure is reported, the budget is spent.

**The race is closed by the database.** Two clicks (or a double-submit) would both read a
cooldown that had expired and both poll. ``SELECT … FOR UPDATE`` on the connection row makes
the second one wait and then see the first one's stamp — the ``docs/PAYMENTS.md`` rule, one
layer down: an idempotency guarantee that lives in application code loses the race the database
would have won.

A refusal that is not the user's fault (not connected, mailbox not opted in, the org has Gmail
switched off) is an ``AppError`` — the screen should not have drawn the button, and the API is
the boundary that says so. A refusal that *is* simply "too soon" is a 200 carrying
``status="cooldown"`` and the seconds left: it is not an error, it is the honest answer "this
feed is already fresh", and it must arrive with ``last_polled_at`` beside it so the button can
keep saying when that was.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select

from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.google.gmail.service import poll_connection
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.google.oauth import SCOPE_GMAIL, google_settings_row

logger = logging.getLogger("schakl.google.gmail")

#: How long a mailbox's owner waits between manual polls. The cron already covers the mailbox
#: every five minutes, so this is the *extra* load a human can ask for: at worst one
#: metadata-only ``history.list`` per minute per connected mailbox, which is nothing beside
#: Gmail's per-project budget — and it stays bounded however hard anyone leans on the button.
MANUAL_POLL_COOLDOWN = timedelta(seconds=60)


class GmailSyncStatus(BaseModel):
    """Everything the button needs to decide whether to draw itself, and what to say."""

    #: This user has a Google connection at all.
    connected: bool = False
    #: The org has the Gmail surface switched on (Instellingen → Google).
    gmail_enabled: bool = False
    #: This user opted their own mailbox in (Instellingen → Account).
    sync_enabled: bool = False
    #: Google actually granted the Gmail scope — an opt-in without it polls nothing.
    scope_granted: bool = False
    #: The grant is alive: a revoked or un-refreshable token reads ``error`` here.
    connection_error: bool = False
    #: When this mailbox was last polled **by anything** — the cron included. This is the
    #: "laatst ververst" the screen prints, so it must not be the manual stamp: a feed the
    #: cron refreshed two minutes ago is two minutes old whoever asked for it.
    last_polled_at: datetime | None = None
    #: All four gates above line up, so a refresh is a call worth making.
    available: bool = False
    #: Seconds until the next manual poll is allowed (0 = now).
    retry_after_seconds: int = 0


class GmailRefreshResult(BaseModel):
    status: Literal["polled", "cooldown", "error"]
    #: How many interactions this poll logged. Always 0 for anything but ``polled``.
    logged: int = 0
    sync: GmailSyncStatus


async def _locked_connection(ctx: RequestContext) -> GoogleConnection | None:
    """The caller's own connection row, locked for the rest of the transaction."""
    return await ctx.session.scalar(
        select(GoogleConnection)
        .where(
            GoogleConnection.org_id == ctx.org.id,
            GoogleConnection.user_id == ctx.user.id,
        )
        .with_for_update()
    )


def _retry_after(connection: GoogleConnection, now: datetime) -> int:
    """Whole seconds left on the cooldown — rounded **up**, so 0 always means "go ahead"."""
    stamped = connection.gmail_manual_poll_at
    if stamped is None:
        return 0
    elapsed = now - stamped
    if elapsed >= MANUAL_POLL_COOLDOWN:
        return 0
    remaining = (MANUAL_POLL_COOLDOWN - elapsed).total_seconds()
    return max(1, int(remaining) + (1 if remaining % 1 else 0))


def _status(
    connection: GoogleConnection | None, *, gmail_enabled: bool, now: datetime
) -> GmailSyncStatus:
    if connection is None:
        return GmailSyncStatus(gmail_enabled=gmail_enabled)
    scope_granted = SCOPE_GMAIL in (connection.scopes or [])
    errored = connection.status != ConnectionStatus.ACTIVE.value
    retry_after = _retry_after(connection, now)
    return GmailSyncStatus(
        connected=True,
        gmail_enabled=gmail_enabled,
        sync_enabled=connection.gmail_sync_enabled,
        scope_granted=scope_granted,
        connection_error=errored,
        last_polled_at=connection.gmail_last_polled_at,
        available=(
            gmail_enabled and connection.gmail_sync_enabled and scope_granted and not errored
        ),
        retry_after_seconds=retry_after,
    )


async def gmail_status(ctx: RequestContext) -> GmailSyncStatus:
    """Read-only: what the interactions screen renders above its list."""
    row = await google_settings_row(ctx.session, ctx.org.id)
    connection = await ctx.session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == ctx.org.id,
            GoogleConnection.user_id == ctx.user.id,
        )
    )
    return _status(
        connection, gmail_enabled=bool(row and row.gmail_enabled), now=datetime.now(UTC)
    )


async def refresh_my_mailbox(ctx: RequestContext) -> GmailRefreshResult:
    """Poll the caller's own mailbox once, within the cooldown."""
    row = await google_settings_row(ctx.session, ctx.org.id)
    gmail_enabled = bool(row and row.gmail_enabled)
    if not gmail_enabled:
        raise AppError("gmail_disabled", "errors.gmail_disabled", status_code=409)

    connection = await _locked_connection(ctx)
    if connection is None:
        raise AppError(
            "google_not_connected", "errors.google_not_connected", status_code=409
        )
    if not connection.gmail_sync_enabled or SCOPE_GMAIL not in (connection.scopes or []):
        raise AppError("gmail_sync_off", "errors.gmail_sync_off", status_code=409)
    if connection.status != ConnectionStatus.ACTIVE.value:
        # Reconnecting is the only fix, and the account card is where that lives — polling a
        # dead grant would just re-notify the owner about a breakage they already know about.
        raise AppError(
            "google_connection_error", "errors.google_connection_error", status_code=409
        )

    now = datetime.now(UTC)
    if _retry_after(connection, now) > 0:
        return GmailRefreshResult(
            status="cooldown",
            sync=_status(connection, gmail_enabled=gmail_enabled, now=now),
        )

    # Stamped before the poll and *outside* its savepoint: a mailbox that raises must still
    # spend its budget, or an erroring grant can be hammered a click at a time.
    connection.gmail_manual_poll_at = now
    await ctx.session.flush()

    logged = 0
    status: Literal["polled", "error"] = "polled"
    try:
        # Its own savepoint: catching a failed statement without one leaves the session
        # poisoned for everything after it (CLAUDE.md §18), and this handler still has to
        # return a body.
        async with ctx.session.begin_nested():
            logged = await poll_connection(ctx.session, ctx.org, connection)
    except Exception:  # noqa: BLE001 — reported to the caller, never a 500 on a refresh button
        logger.exception(
            "Manual Gmail refresh failed for connection %s (org %s)",
            connection.id,
            ctx.org.id,
        )
        status = "error"

    return GmailRefreshResult(
        status=status,
        logged=logged,
        sync=_status(connection, gmail_enabled=gmail_enabled, now=datetime.now(UTC)),
    )


__all__ = [
    "MANUAL_POLL_COOLDOWN",
    "GmailRefreshResult",
    "GmailSyncStatus",
    "gmail_status",
    "refresh_my_mailbox",
]
