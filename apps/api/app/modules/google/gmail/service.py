"""The gmail ingest pipeline: historyId polling, matched metadata-first logging (GOOGLE.md §6).

Never a mailbox sync. Per poll, per connection: pull the message ids added since the stored
``historyId``, fetch **metadata only** (headers + snippet + labels), and log the ones whose
participants match a known contact — pending by default, so the mailbox owner approves before
any content is shared. Bodies are fetched separately, only after approval (or immediately when
the org runs ``auto_approve``).

Skips, in order: already imported (this mailbox), already logged by a colleague's mailbox
(the RFC-822 ``Message-ID`` dedup), suppressed (rejected earlier), the owner's excluded label,
colleague-only mail, and no contact match. First poll stores the current ``historyId`` and
imports nothing — connecting a mailbox is opt-in *going forward*, never a retroactive import.
"""

from __future__ import annotations

import base64
import logging
import uuid
from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext
from app.core.models import Org
from app.core.portal import portal_user_ids
from app.modules.google.client import acting_as, mark_connection_error
from app.modules.google.gmail import matching
from app.modules.google.gmail.models import GmailSuppression
from app.modules.google.models import GoogleConnection, GoogleSettings
from app.modules.google.oauth import google_settings_row
from app.modules.interactions import system as interactions_system

logger = logging.getLogger("schakl.google.gmail")

GMAIL_API = "https://www.googleapis.com/gmail/v1/users/me"
PENDING_EVENT = "interactions.email_pending"
_HISTORY_PAGE_SIZE = 100
_METADATA_HEADERS = ("From", "To", "Cc", "Subject", "Message-ID")


def deep_link(message_id: str) -> str:
    return f"https://mail.google.com/mail/u/0/#all/{message_id}"


class ResyncNeeded(Exception):
    """The stored historyId expired (Gmail keeps about a week) — re-baseline, no backfill."""


async def poll_connection(
    session: AsyncSession, org: Org, connection: GoogleConnection
) -> int:
    """One poll for one mailbox; returns how many interactions were logged."""
    settings_row = await google_settings_row(session, org.id)
    if settings_row is None or not settings_row.gmail_enabled:
        return 0
    try:
        async with acting_as(session, org, connection) as client:
            if not connection.gmail_history_id:
                await _baseline(client, connection)
                await session.flush()
                return 0
            try:
                message_ids, latest_history_id = await _history_since(client, connection)
            except ResyncNeeded:
                await _baseline(client, connection)
                await session.flush()
                return 0
            excluded_label_id = await _excluded_label_id(client, connection)
            logged = 0
            for message_id in message_ids:
                try:
                    # Savepoint per message: a failed ingest rolls back only its own writes,
                    # so a DB error cannot abort the transaction for the messages after it.
                    async with session.begin_nested():
                        logged += await _ingest_message(
                            session,
                            org,
                            connection,
                            settings_row,
                            client,
                            message_id,
                            excluded_label_id,
                        )
                except Exception as ingest_exc:  # noqa: BLE001 — a poison message must not wedge the mailbox
                    # A dead grant is the *connection's* problem: let the outer handler mark
                    # it, so the owner is notified instead of every message "failing".
                    from app.modules.google.client import is_oauth_error

                    if await is_oauth_error(ingest_exc):
                        raise
                    # historyId only advances after the loop, so a message that kept raising
                    # would re-abort every poll and silently stop the whole feed. Skipping it
                    # loses one email (loudly, below); wedging loses every email after it.
                    logger.exception(
                        "Gmail ingest failed for message %s on connection %s (org %s); skipped",
                        message_id,
                        connection.id,
                        org.id,
                    )
            if latest_history_id:
                connection.gmail_history_id = latest_history_id[:32]
    except Exception as exc:
        from app.modules.google.client import is_oauth_error

        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
            return 0
        raise
    connection.gmail_last_polled_at = datetime.now(UTC)
    await session.flush()
    return logged


async def _baseline(client, connection: GoogleConnection) -> None:
    response = await client.get(f"{GMAIL_API}/profile")
    response.raise_for_status()
    history_id = response.json().get("historyId")
    if history_id:
        connection.gmail_history_id = str(history_id)[:32]


async def _history_since(
    client, connection: GoogleConnection
) -> tuple[list[str], str | None]:
    message_ids: list[str] = []
    latest: str | None = None
    page_token: str | None = None
    while True:
        params: dict[str, str] = {
            "startHistoryId": connection.gmail_history_id or "",
            "historyTypes": "messageAdded",
            "maxResults": str(_HISTORY_PAGE_SIZE),
        }
        if page_token:
            params["pageToken"] = page_token
        response = await client.get(f"{GMAIL_API}/history", params=params)
        if response.status_code == 404:
            raise ResyncNeeded
        response.raise_for_status()
        body = response.json()
        latest = str(body.get("historyId") or latest or "") or None
        for entry in body.get("history", []):
            for added in entry.get("messagesAdded", []):
                message_id = (added.get("message") or {}).get("id")
                if message_id and message_id not in message_ids:
                    message_ids.append(message_id)
        page_token = body.get("nextPageToken")
        if not page_token:
            return message_ids, latest


async def _excluded_label_id(client, connection: GoogleConnection) -> str | None:
    """The owner's opt-out label, resolved name → Gmail label id once per poll."""
    if not connection.gmail_excluded_label:
        return None
    response = await client.get(f"{GMAIL_API}/labels")
    response.raise_for_status()
    wanted = connection.gmail_excluded_label.strip().lower()
    for label in response.json().get("labels", []):
        if (label.get("name") or "").strip().lower() == wanted:
            return label.get("id")
    return None


async def _ingest_message(
    session: AsyncSession,
    org: Org,
    connection: GoogleConnection,
    settings_row: GoogleSettings,
    client,
    message_id: str,
    excluded_label_id: str | None,
) -> int:
    ctx = SystemContext(org=org, session=session)
    if await interactions_system.gmail_message_seen(ctx, connection.user_id, message_id):
        return 0
    if await _suppressed(session, org.id, connection.id, message_id=message_id):
        return 0

    response = await client.get(
        f"{GMAIL_API}/messages/{message_id}",
        params={
            "format": "metadata",
            "metadataHeaders": list(_METADATA_HEADERS),
        },
    )
    if response.status_code == 404:
        return 0
    response.raise_for_status()
    message = response.json()
    label_ids = message.get("labelIds") or []
    if not matching.is_relevant(label_ids, excluded_label_id):
        return 0
    thread_id = message.get("threadId")
    if thread_id and await _suppressed(session, org.id, connection.id, thread_id=thread_id):
        return 0

    headers = matching.headers_map(message)
    rfc822_id = (headers.get("Message-ID") or "").strip()[:512] or None
    if rfc822_id and await interactions_system.rfc822_seen(ctx, rfc822_id):
        return 0  # a colleague's mailbox already logged this email — one timeline entry

    participants = matching.parse_participants(headers)
    if not participants:
        return 0
    internal = matching.internal_only(participants, await _member_emails(session, org.id))
    if internal and not settings_row.gmail_log_internal:
        return 0
    matches = await _match_contacts(session, org.id, participants)
    if not matches and not internal:
        # External mail still needs a known contact; without the internal opt-in nothing
        # changes here — every newsletter and cold email stays out.
        return 0

    inherited = (
        await interactions_system.thread_mappings(ctx, thread_id) if thread_id else None
    )
    mappings = dict(inherited) if inherited else matching.resolve_mappings(matches)
    pending = matching.decide_status(
        settings_row.gmail_approval_mode,
        settings_row.gmail_thread_followup,
        inherited=inherited is not None,
    )
    if internal and not mappings:
        # An opted-in internal mail has no contact to map from, so there is nothing to
        # auto-file it under: it always waits for its owner, whatever the approval mode.
        # Once approved onto a client/project, thread follow-ups inherit as usual.
        pending = True

    internal_date = message.get("internalDate")
    occurred_at = (
        datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
        if internal_date
        else datetime.now(UTC)
    )
    subject = headers.get("Subject") or None
    row = await interactions_system.record_email(
        ctx,
        owner_user_id=connection.user_id,
        owner_name=await _owner_name(session, connection.user_id),  # snapshot rule (#64)
        occurred_at=occurred_at,
        subject=subject,
        snippet=(message.get("snippet") or "").strip() or None,
        direction=matching.direction_of(label_ids),
        participants=participants,
        gmail_message_id=message_id,
        gmail_thread_id=thread_id,
        rfc822_message_id=rfc822_id,
        deep_link=deep_link(message_id),
        pending=pending,
        mappings=mappings,
    )

    if pending:
        await _notify_pending(ctx, row, subject)
    else:
        # Logged at birth (auto-approve / trusted thread): the body may load inline — we are
        # already in worker context, no user is waiting.
        await _fetch_body_with(client, ctx, row.id, message_id, row.owner_user_id)
    return 1


async def _notify_pending(ctx: SystemContext, row, subject: str | None) -> None:
    from app.modules.notifications.service import NotificationService

    await NotificationService(ctx).ingest(
        PENDING_EVENT,
        "interaction",
        row.id,
        {
            "subject": subject or "",
            "company_id": str(row.company_id) if row.company_id else None,
            "contact_id": str(row.contact_id) if row.contact_id else None,
            "_recipients": [row.owner_user_id],
            "_dedup_key": f"gmail-pending:{row.owner_user_id}:{row.gmail_message_id}",
        },
    )


async def _suppressed(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    message_id: str | None = None,
    thread_id: str | None = None,
) -> bool:
    conditions = [
        GmailSuppression.org_id == org_id,
        GmailSuppression.connection_id == connection_id,
    ]
    if message_id is not None:
        conditions.append(GmailSuppression.gmail_message_id == message_id)
    if thread_id is not None:
        conditions.append(GmailSuppression.gmail_thread_id == thread_id)
    return (
        await session.scalar(select(GmailSuppression.id).where(*conditions).limit(1))
    ) is not None


async def _owner_name(session: AsyncSession, user_id: uuid.UUID) -> str | None:
    row = (
        await session.execute(
            text("SELECT full_name, email FROM users WHERE id = :uid"), {"uid": user_id}
        )
    ).first()
    if row is None:
        return None
    return row[0] or row[1]


async def _member_emails(session: AsyncSession, org_id: uuid.UUID) -> set[str]:
    """The *staff* addresses, for the colleague-chatter filter (``internal_only``).

    A portal login (#193) is an ordinary membership whose user is a client's contact — so a
    naive all-memberships set makes every portal-invited client look like a colleague, and
    ``internal_only`` then silently drops their entire correspondence (polls succeed,
    ``logged:0`` forever). Portal users are excluded through the core seam; they keep
    matching as *contacts*, which is what they are.
    """
    rows = await session.execute(
        text(
            "SELECT u.id, lower(u.email) FROM users u "
            "JOIN memberships m ON m.user_id = u.id WHERE m.org_id = :oid"
        ),
        {"oid": org_id},
    )
    pairs = [(row[0], row[1]) for row in rows]
    portal = await portal_user_ids(session, org_id, {uid for uid, _ in pairs})
    return {email for uid, email in pairs if uid not in portal}


async def _match_contacts(
    session: AsyncSession, org_id: uuid.UUID, participants: list[dict[str, str]]
) -> list[matching.ContactMatch]:
    """Participant addresses → contacts (+ their companies, oldest link first). Bare-table
    lookups, never a contacts-module import (§6)."""
    addresses = sorted({p["email"] for p in participants})
    if not addresses:
        return []
    contact_rows = await session.execute(
        text(
            "SELECT id FROM contacts WHERE org_id = :oid AND lower(email) = ANY(:addrs) "
            "ORDER BY created_at"
        ),
        {"oid": org_id, "addrs": addresses},
    )
    matches: list[matching.ContactMatch] = []
    for (contact_id,) in contact_rows:
        company_rows = await session.execute(
            text(
                "SELECT company_id FROM company_contacts "
                "WHERE org_id = :oid AND contact_id = :cid ORDER BY created_at"
            ),
            {"oid": org_id, "cid": contact_id},
        )
        matches.append(
            matching.ContactMatch(
                contact_id=contact_id,
                company_ids=[row[0] for row in company_rows],
            )
        )
    return matches


# --------------------------------------------------------------------------- #
# Body fetch — after approval (or inline on auto-approve)
# --------------------------------------------------------------------------- #
async def fetch_body(
    session: AsyncSession, org: Org, interaction_id: uuid.UUID
) -> bool:
    ctx = SystemContext(org=org, session=session)
    ref = await interactions_system.email_ref(ctx, interaction_id)
    if ref is None:
        return False
    owner_user_id, message_id = ref
    connection = await session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == org.id, GoogleConnection.user_id == owner_user_id
        )
    )
    if connection is None:
        return False
    try:
        async with acting_as(session, org, connection) as client:
            return await _fetch_body_with(
                client, ctx, interaction_id, message_id, owner_user_id
            )
    except Exception as exc:
        from app.modules.google.client import is_oauth_error

        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
            return False
        raise


async def _fetch_body_with(
    client, ctx: SystemContext, interaction_id, message_id: str, owner_user_id
) -> bool:
    response = await client.get(
        f"{GMAIL_API}/messages/{message_id}", params={"format": "full"}
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    payload = response.json().get("payload") or {}
    body_text = matching.extract_text(payload)
    if body_text is None:
        return False
    await interactions_system.set_body(ctx, interaction_id, body_text)
    # Attachments ride the same approval-time fetch (#180): the full payload already names
    # them, so this is one extra call per attachment, never per message. A pending row never
    # reaches this code — reject must leave no stored bytes anywhere.
    await _store_attachments(client, ctx, interaction_id, message_id, payload, owner_user_id)
    return True


async def _store_attachments(
    client, ctx: SystemContext, interaction_id, message_id: str, payload: dict, owner_user_id
) -> None:
    from app.core.storage import system as storage_system

    parts = matching.attachment_parts(payload)
    if not parts:
        return
    # The bodyless sweep may re-offer a fetch; the same attachments must not store twice.
    if await storage_system.entity_has_files(ctx, "interaction", interaction_id):
        return
    for part in parts:
        attachment_id = (part.get("body") or {}).get("attachmentId")
        response = await client.get(
            f"{GMAIL_API}/messages/{message_id}/attachments/{attachment_id}"
        )
        if response.status_code >= 400:
            logger.warning("gmail attachment fetch failed for %s", interaction_id)
            continue
        data = base64.urlsafe_b64decode(response.json().get("data") or "")
        stored = await storage_system.store_system_file(
            ctx,
            filename=str(part.get("filename") or "bijlage"),
            content_type=str(part.get("mimeType") or "application/octet-stream"),
            data=data,
            entity_type="interaction",
            entity_id=interaction_id,
            created_by_user_id=owner_user_id,
        )
        if stored is None:
            # Type/size validation skipped it — worth a log line, never a failed body fetch.
            logger.info(
                "gmail attachment skipped (type/size) for %s: %s",
                interaction_id,
                part.get("filename"),
            )


# --------------------------------------------------------------------------- #
# Suppression (the interaction.rejected subscriber's write)
# --------------------------------------------------------------------------- #
async def suppress(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    message_id: str | None,
    thread_id: str | None,
) -> None:
    if message_id and not await _suppressed(
        session, org_id, connection_id, message_id=message_id
    ):
        session.add(
            GmailSuppression(
                org_id=org_id, connection_id=connection_id, gmail_message_id=message_id
            )
        )
    if thread_id:
        already = await session.scalar(
            select(GmailSuppression.id)
            .where(
                GmailSuppression.org_id == org_id,
                GmailSuppression.connection_id == connection_id,
                GmailSuppression.gmail_thread_id == thread_id,
                GmailSuppression.gmail_message_id.is_(None),
            )
            .limit(1)
        )
        if already is None:
            session.add(
                GmailSuppression(
                    org_id=org_id, connection_id=connection_id, gmail_thread_id=thread_id
                )
            )
    await session.flush()
