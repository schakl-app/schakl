"""Bus subscribers: the review flow's Outlook half — the owner's decisions.

Both run in the interactions service's transaction and act **only on this feed's rows**: the
payload names its ``source``, and a Gmail row is the Gmail feed's to fetch or suppress.

- ``interaction.approved`` → offer the body fetch to the worker (never an HTTP call inline).
- ``interaction.rejected`` → write the suppression rows *in the same transaction*, so the
  rejection and its "never again" commit together. The interactions service deletes the row.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.events import EmitContext
from app.integrations.microsoft.client import connection_for
from app.integrations.microsoft.outlook.service import SOURCE, suppress

logger = logging.getLogger("schakl.microsoft.outlook")


async def handle_interaction_approved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    if payload.get("source") != SOURCE:
        return
    if not payload.get("gmail_message_id") or not payload.get("interaction_id"):
        return
    from datetime import timedelta

    from app.core.jobs import enqueue

    try:
        await enqueue(
            "outlook_fetch_body",
            str(ctx.org.id),
            str(payload["interaction_id"]),
            _defer_by=timedelta(seconds=2),
        )
    except Exception:  # noqa: BLE001 — the bodyless sweep re-offers
        logger.warning(
            "outlook body-fetch enqueue failed for %s; sweep will retry",
            payload["interaction_id"],
        )


async def handle_interaction_rejected(ctx: EmitContext, payload: dict[str, Any]) -> None:
    if payload.get("source") != SOURCE:
        return
    message_id = payload.get("gmail_message_id")
    owner_user_id = payload.get("owner_user_id")
    if not message_id or not owner_user_id:
        return
    connection = await connection_for(ctx.session, ctx.org.id, owner_user_id)
    if connection is None:
        return  # mailbox since disconnected: nothing will re-import it anyway
    await suppress(
        ctx.session,
        ctx.org.id,
        connection.id,
        message_id=str(message_id),
        conversation_id=(
            str(payload["gmail_thread_id"])
            if payload.get("suppress_thread") and payload.get("gmail_thread_id")
            else None
        ),
    )
