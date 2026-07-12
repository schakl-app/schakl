"""Bus subscribers: the review flow's Google half (issue #22, the owner's decisions).

Both run in the interactions service's transaction:

- ``interaction.approved`` → offer the body fetch to the worker (never an HTTP call inline —
  the emitter is a live user request). The bodyless-sweep cron backstops a lost offer.
- ``interaction.rejected`` → write the suppression rows *in the same transaction*, so the
  rejection and its "never again" commit together. The interactions service deletes the row.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.events import EmitContext
from app.modules.google.client import connection_for
from app.modules.google.gmail.service import suppress

logger = logging.getLogger("schakl.google.gmail")


async def handle_interaction_approved(ctx: EmitContext, payload: dict[str, Any]) -> None:
    if not payload.get("gmail_message_id") or not payload.get("interaction_id"):
        return
    from datetime import timedelta

    from app.core.jobs import enqueue

    try:
        await enqueue(
            "google_gmail_fetch_body",
            str(ctx.org.id),
            str(payload["interaction_id"]),
            _defer_by=timedelta(seconds=2),
        )
    except Exception:  # noqa: BLE001 — the bodyless sweep re-offers
        logger.warning(
            "gmail body-fetch enqueue failed for %s; sweep will retry",
            payload["interaction_id"],
        )


async def handle_interaction_rejected(ctx: EmitContext, payload: dict[str, Any]) -> None:
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
        thread_id=(
            str(payload["gmail_thread_id"])
            if payload.get("suppress_thread") and payload.get("gmail_thread_id")
            else None
        ),
    )
