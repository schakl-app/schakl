"""The Outlook ingest pipeline: cursor polling, matched metadata-first logging (MICROSOFT.md §6).

Never a mailbox sync. Per poll, per connection: list the messages that arrived since the stored
cursor — **metadata only** (``$select`` names headers, preview and folder, never the body) — and
log the ones whose participants match a known contact outside the agency, pending by default so
the mailbox owner approves before any content is shared. Bodies are fetched separately, only
after approval (or immediately when the org runs ``auto_approve``).

**Why a timestamp cursor and not Graph's delta.** ``/me/mailFolders/{id}/messages/delta`` is
per folder, and an Outlook rule that files a client's mail into "Klanten/Acme" on arrival moves it
out of the Inbox before any poll sees it — the very mail this feed exists for. ``/me/messages``
reads the whole mailbox, so the cursor is the newest ``receivedDateTime`` seen, re-read with a
few minutes of overlap because a message can land with a timestamp older than the one that
arrived before it. The overlap costs nothing: a message already logged, suppressed or declined
is declined again in the same way, and only the first answer ever writes a row.

**The skip chain is :func:`classify`, and it is a function rather than a shape** — the same
ordered gates as the Gmail feed's, answered in the shared vocabulary (``app/core/mailbox/gates``),
so the manual importer's explainer can ask what the poller would do without doing it.

First poll stores the current instant and imports nothing — connecting a mailbox is opt-in
*going forward*, never a retroactive import.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext
from app.core.htmlmd import html_to_markdown, referenced_cids, rewrite_cid_images
from app.core.mailbox.internals import (
    Internals,
    Mailbox,
    colleagues_on,
    load_internals,
    match_contacts,
    owner_name,
)
from app.core.models import Org
from app.integrations.microsoft.client import acting_as, mark_connection_error
from app.integrations.microsoft.models import (
    ConnectionStatus,
    MicrosoftConnection,
    MicrosoftSettings,
)
from app.integrations.microsoft.oauth import has_mail_scope, microsoft_settings_row
from app.integrations.microsoft.outlook import matching
from app.integrations.microsoft.outlook.gates import (
    PERSISTED_REASONS,
    Decision,
    GateCache,
    SkipReason,
    skip_row_values,
)
from app.integrations.microsoft.outlook.matching import WellKnownFolders
from app.integrations.microsoft.outlook.models import OutlookSkip, OutlookSuppression
from app.modules.interactions import system as interactions_system
from app.modules.interactions.models import InteractionSource

logger = logging.getLogger("schakl.microsoft.outlook")

SOURCE = InteractionSource.OUTLOOK.value
PENDING_EVENT = "interactions.email_pending"
#: What a listing asks for: headers, preview, folder, categories — never ``body``.
MESSAGE_SELECT = (
    "id,conversationId,internetMessageId,subject,bodyPreview,receivedDateTime,"
    "sentDateTime,from,toRecipients,ccRecipients,categories,isDraft,parentFolderId,"
    "webLink,hasAttachments"
)
_PAGE_SIZE = 100
#: How far behind the cursor a poll re-reads (see the module docstring).
CURSOR_OVERLAP = timedelta(minutes=5)
#: The four well-known folders a message's state is read off. Resolved by name, per poll.
_WELL_KNOWN = (
    ("junk", "junkemail"),
    ("deleted", "deleteditems"),
    ("drafts", "drafts"),
    ("sent", "sentitems"),
)
#: Bodies arrive as HTML when the message has one; the client-wide ``Prefer`` (UTC dates) is
#: restated because a per-request ``Prefer`` replaces it rather than adding to it.
BODY_PREFER = 'outlook.timezone="UTC", outlook.body-content-type="html"'
FILE_ATTACHMENT = "#microsoft.graph.fileAttachment"


def graph_iso(instant: datetime) -> str:
    """An instant as Graph's ``$filter`` wants it: second precision, ``Z``."""
    return instant.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def deep_link(message: dict[str, Any]) -> str | None:
    """Graph hands every message its own Outlook on the web URL; nothing is constructed."""
    return (message.get("webLink") or "")[:500] or None


# --------------------------------------------------------------------------- #
# The poll
# --------------------------------------------------------------------------- #
async def poll_connection(session: AsyncSession, org: Org, connection: MicrosoftConnection) -> int:
    """One poll for one mailbox; returns how many interactions were logged."""
    settings_row = await microsoft_settings_row(session, org.id)
    if settings_row is None or not settings_row.outlook_enabled:
        return 0
    try:
        async with acting_as(session, org, connection) as client:
            if connection.outlook_cursor_at is None:
                connection.outlook_cursor_at = datetime.now(UTC)
                await session.flush()
                return 0
            folders = await well_known_folders(client)
            messages, newest = await _messages_since(
                client, connection.outlook_cursor_at - CURSOR_OVERLAP
            )
            internals = await _internals(session, org.id)
            logged = 0
            for message in messages:
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
                            message,
                            folders,
                            internals,
                        )
                except Exception as ingest_exc:  # noqa: BLE001 — a poison message must not wedge the mailbox
                    from app.integrations.microsoft.client import is_oauth_error

                    if await is_oauth_error(ingest_exc):
                        raise
                    # The cursor only advances after the loop, so a message that kept raising
                    # would re-abort every poll and silently stop the whole feed. Skipping it
                    # loses one email (loudly, below); wedging loses every email after it.
                    logger.exception(
                        "Outlook ingest failed for message %s on connection %s (org %s); skipped",
                        message.get("id"),
                        connection.id,
                        org.id,
                    )
                    await _record_skip(
                        session,
                        org,
                        connection,
                        message,
                        SkipReason.INGEST_ERROR,
                        {"error": type(ingest_exc).__name__},
                    )
            if newest is not None and newest > connection.outlook_cursor_at:
                connection.outlook_cursor_at = newest
    except Exception as exc:
        from app.integrations.microsoft.client import is_oauth_error

        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
            return 0
        raise
    connection.outlook_last_polled_at = datetime.now(UTC)
    await session.flush()
    return logged


async def well_known_folders(client) -> WellKnownFolders:
    """Resolve the four well-known folders' ids for this mailbox — one call each, per poll.
    A folder Graph will not name (a mailbox with no Junk folder) simply never matches."""
    found: dict[str, str | None] = {}
    for attribute, name in _WELL_KNOWN:
        response = await client.get(f"/me/mailFolders/{name}", params={"$select": "id"})
        if response.status_code == 404:
            found[attribute] = None
            continue
        response.raise_for_status()
        found[attribute] = (response.json() or {}).get("id") or None
    return WellKnownFolders(**found)


async def _messages_since(client, since: datetime) -> tuple[list[dict], datetime | None]:
    """Every message received at or after ``since``, oldest first, and the newest instant seen."""
    messages: list[dict] = []
    newest: datetime | None = None
    next_link: str | None = None
    params: dict[str, str] = {
        "$filter": f"receivedDateTime ge {graph_iso(since)}",
        "$orderby": "receivedDateTime asc",
        "$top": str(_PAGE_SIZE),
        "$select": MESSAGE_SELECT,
    }
    while True:
        # A ``@odata.nextLink`` is an absolute URL carrying its own query; it is followed
        # verbatim, and re-sending the parameters would double them.
        if next_link:
            response = await client.get(next_link)
        else:
            response = await client.get("/me/messages", params=params)
        response.raise_for_status()
        body = response.json() or {}
        for message in body.get("value") or []:
            if not message.get("id"):
                continue
            messages.append(message)
            received = matching.parse_graph_datetime(message.get("receivedDateTime") or "")
            if received is not None and (newest is None or received > newest):
                newest = received
        next_link = body.get("@odata.nextLink")
        if not next_link:
            return messages, newest


async def message_already_here(
    session: AsyncSession,
    org: Org,
    connection: MicrosoftConnection,
    message_id: str,
    cache: GateCache | None = None,
) -> SkipReason | None:
    """The two answers that need no message content — asked before anything else."""
    ctx = SystemContext(org=org, session=session)
    if cache is not None and cache.logged_message_ids is not None:
        if message_id in cache.logged_message_ids:
            return SkipReason.ALREADY_LOGGED
    elif await interactions_system.gmail_message_seen(ctx, connection.user_id, message_id):
        return SkipReason.ALREADY_LOGGED
    if cache is not None and cache.suppressed_message_ids is not None:
        if message_id in cache.suppressed_message_ids:
            return SkipReason.SUPPRESSED_MESSAGE
    elif await _suppressed(session, org.id, connection.id, message_id=message_id):
        return SkipReason.SUPPRESSED_MESSAGE
    return None


async def classify(
    session: AsyncSession,
    org: Org,
    connection: MicrosoftConnection,
    settings_row: MicrosoftSettings,
    message: dict,
    folders: WellKnownFolders,
    internals: Internals,
    cache: GateCache | None = None,
) -> Decision:
    """Would this message be logged, and if not, which gate stopped it?

    **Decides; never writes and never fetches** — the Gmail feed's rule, restated because it is
    the whole point: the manual importer asks this about one message a person is looking at,
    and an explainer that drifts from the ingest answers confidently and wrongly. The gates are
    ordered and short-circuiting, so the returned reason is the first one that fired.
    """
    ctx = SystemContext(org=org, session=session)
    message_id = str(message.get("id") or "")

    already = await message_already_here(session, org, connection, message_id, cache)
    if already is not None:
        return Decision(reason=already)

    if matching.is_not_a_message(message, folders):
        return Decision(reason=SkipReason.NOT_A_MESSAGE)
    category = matching.excluded_category_on(message, connection.outlook_excluded_category)
    if category is not None:
        return Decision(reason=SkipReason.EXCLUDED_CATEGORY, detail={"label": category})

    conversation_id = message.get("conversationId")
    if conversation_id:
        if cache is not None and cache.suppressed_thread_ids is not None:
            suppressed_thread = conversation_id in cache.suppressed_thread_ids
        else:
            suppressed_thread = await _suppressed(
                session, org.id, connection.id, conversation_id=conversation_id
            )
        if suppressed_thread:
            return Decision(reason=SkipReason.SUPPRESSED_THREAD)

    rfc822_id = matching.rfc822_id_of(message)
    if rfc822_id:
        if cache is not None and cache.logged_rfc822_ids is not None:
            logged_elsewhere = rfc822_id in cache.logged_rfc822_ids
        else:
            logged_elsewhere = await interactions_system.rfc822_seen(ctx, rfc822_id)
        if logged_elsewhere:
            # A colleague's mailbox already logged this email — one timeline entry.
            return Decision(reason=SkipReason.LOGGED_ELSEWHERE)

    participants = matching.participants_of(message)
    if not participants:
        return Decision(reason=SkipReason.NO_PARTICIPANTS)
    if _defer_to_owner_mailbox(connection, message, folders, participants, internals):
        owner = matching.intended_owner(participants, internals.owner_by_email.keys())
        return Decision(reason=SkipReason.DEFERRED_TO_OWNER, detail={"owner": owner or ""})

    internal = matching.internal_only(participants, internals.ours)
    if internal and not settings_row.outlook_log_internal:
        return Decision(reason=SkipReason.INTERNAL_ONLY)
    addresses = tuple(sorted({p["email"] for p in participants}))
    if cache is not None and addresses in cache.contacts_by_addresses:
        matches = cache.contacts_by_addresses[addresses]
    else:
        matches = await match_contacts(session, org.id, participants, internals)
        if cache is not None:
            cache.contacts_by_addresses[addresses] = matches
    if not internal and not matching.has_external_match(matches, internals.company_ids):
        # A mail with an outsider on it still needs that outsider to be a contact we know —
        # and "known" has to mean known *and outside* (#324).
        return Decision(reason=SkipReason.NO_EXTERNAL_MATCH)

    if not conversation_id:
        inherited = None
    elif cache is not None and conversation_id in cache.mappings_by_thread:
        inherited = cache.mappings_by_thread[conversation_id]
    else:
        inherited = await interactions_system.thread_mappings(ctx, conversation_id)
        if cache is not None:
            cache.mappings_by_thread[conversation_id] = inherited
    mappings = (
        dict(inherited)
        if inherited
        else matching.resolve_mappings(matches, internal_company_ids=internals.company_ids)
    )
    pending = matching.decide_status(
        settings_row.outlook_approval_mode,
        settings_row.outlook_thread_followup,
        inherited=inherited is not None,
    )
    if internal and not mappings:
        # An opted-in internal mail has no contact to map from: it always waits for its owner.
        pending = True
    return Decision(mappings=mappings, pending=pending)


async def _ingest_message(
    session: AsyncSession,
    org: Org,
    connection: MicrosoftConnection,
    settings_row: MicrosoftSettings,
    client,
    message: dict,
    folders: WellKnownFolders,
    internals: Internals,
) -> int:
    """Ask :func:`classify` what to do with one listed message, and do that.

    The listing already carries every header the gates read, so — unlike the Gmail feed —
    there is no per-message fetch before the decision; the body is the only thing fetched, and
    only for a row logged at birth.
    """
    ctx = SystemContext(org=org, session=session)
    message_id = str(message.get("id") or "")
    decision = await classify(session, org, connection, settings_row, message, folders, internals)
    if not decision.logs:
        await _record_skip(session, org, connection, message, decision.reason, decision.detail)
        logger.debug(
            "Outlook skipped message %s on %s (org %s): %s",
            message_id,
            connection.email,
            org.id,
            decision.reason,
        )
        return 0

    participants = matching.participants_of(message)
    reviewers = colleagues_on(participants, internals) - {connection.user_id}
    row = await interactions_system.record_email(
        ctx,
        owner_user_id=connection.user_id,
        owner_name=await owner_name(session, connection.user_id),  # snapshot rule (#64)
        occurred_at=matching.occurred_at_of(message),
        subject=message.get("subject") or None,
        snippet=matching.clean_snippet(message.get("bodyPreview")),
        direction=matching.direction_of(
            message,
            folders,
            sender_internal=matching.sender_of(participants) in internals.owner_by_email,
        ),
        participants=participants,
        gmail_message_id=message_id,
        gmail_thread_id=message.get("conversationId"),
        rfc822_message_id=matching.rfc822_id_of(message),
        deep_link=deep_link(message),
        pending=decision.pending,
        mappings=decision.mappings,
        reviewer_user_ids=reviewers,
        source=SOURCE,
    )
    if decision.pending:
        await _notify_pending(ctx, row, message.get("subject") or None, reviewers)
    else:
        # Logged at birth (auto-approve / trusted thread): the body may load inline — we are
        # already in worker context, no user is waiting.
        await _fetch_body_with(client, ctx, row.id, message_id, row.owner_user_id)
    return 1


async def _notify_pending(
    ctx: SystemContext, row, subject: str | None, reviewers: set[uuid.UUID] = frozenset()
) -> None:
    """One event, every reviewer a recipient — whoever decides first retires it for all."""
    from app.modules.notifications.service import NotificationService

    await NotificationService(ctx).ingest(
        PENDING_EVENT,
        "interaction",
        row.id,
        {
            "subject": subject or "",
            "company_id": str(row.company_id) if row.company_id else None,
            "contact_id": str(row.contact_id) if row.contact_id else None,
            "_recipients": [row.owner_user_id, *sorted(reviewers, key=str)],
            "_dedup_key": f"outlook-pending:{row.owner_user_id}:{row.gmail_message_id}",
        },
    )


#: How long an ``outlook_skips`` row is kept — the Gmail feed's window, for the same reason.
SKIP_RETENTION_DAYS = 60


async def reap_skips(org: Org, session: AsyncSession) -> None:
    """Drop this org's expired skip rows — the retention half of storing any at all."""
    cutoff = datetime.now(UTC) - timedelta(days=SKIP_RETENTION_DAYS)
    await session.execute(
        delete(OutlookSkip).where(OutlookSkip.org_id == org.id, OutlookSkip.created_at < cutoff)
    )


async def _record_skip(
    session: AsyncSession,
    org: Org,
    connection: MicrosoftConnection,
    message: dict,
    reason: SkipReason | None,
    detail: dict[str, str],
) -> None:
    """Persist the two skips a person would never know to go looking for — and only those.
    Upserted on the natural key, so a message re-offered on every poll (the cursor overlap
    guarantees that) leaves one row and the reaper's window means what it says."""
    if reason not in PERSISTED_REASONS:
        return
    values = skip_row_values(
        reason,
        message_id=str(message.get("id") or ""),
        conversation_id=message.get("conversationId"),
        detail=detail,
    )
    if not values["message_id"]:
        return
    await session.execute(
        pg_insert(OutlookSkip)
        .values(org_id=org.id, connection_id=connection.id, **values)
        .on_conflict_do_update(
            index_elements=[OutlookSkip.org_id, OutlookSkip.connection_id, OutlookSkip.message_id],
            set_={"reason": values["reason"], "detail": values["detail"]},
        )
    )


async def _suppressed(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    message_id: str | None = None,
    conversation_id: str | None = None,
) -> bool:
    conditions = [
        OutlookSuppression.org_id == org_id,
        OutlookSuppression.connection_id == connection_id,
    ]
    if message_id is not None:
        conditions.append(OutlookSuppression.message_id == message_id)
    if conversation_id is not None:
        conditions.append(OutlookSuppression.conversation_id == conversation_id)
    return (
        await session.scalar(select(OutlookSuppression.id).where(*conditions).limit(1))
    ) is not None


# --------------------------------------------------------------------------- #
# Who counts as us — the shared composition, this feed's contribution
# --------------------------------------------------------------------------- #
async def outlook_mailboxes(session: AsyncSession, org_id: uuid.UUID) -> list[Mailbox]:
    """This provider's mailboxes for :mod:`app.core.mailbox.internals`: the address each grant
    was made with, and whether that mailbox genuinely polls — active, opted in, holding the
    mail scope. The same predicate the cron offers on (``jobs.outlook_poll``)."""
    rows = await session.execute(
        select(
            MicrosoftConnection.user_id,
            MicrosoftConnection.email,
            MicrosoftConnection.status,
            MicrosoftConnection.outlook_sync_enabled,
            MicrosoftConnection.scopes,
        )
        .where(MicrosoftConnection.org_id == org_id)
        .order_by(MicrosoftConnection.created_at)
    )
    return [
        Mailbox(
            email=email.lower(),
            user_id=user_id,
            syncing=(
                status == ConnectionStatus.ACTIVE.value
                and bool(sync_enabled)
                and has_mail_scope(scopes)
            ),
        )
        for user_id, email, status, sync_enabled, scopes in rows
    ]


_internals = load_internals


def _defer_to_owner_mailbox(
    connection: MicrosoftConnection,
    message: dict,
    folders: WellKnownFolders,
    participants: list[dict[str, str]],
    internals: Internals,
) -> bool:
    """Is this copy somebody else's, and will their own mailbox log it?

    The Gmail feed's rule with Sent Items in place of the ``SENT`` label: a mailbox does not
    give away its own outgoing mail, a copy naming no colleague cannot pick an owner, and
    standing aside for a mailbox that does not poll — on *either* provider, which is what the
    shared composition answers — would drop the email outright.
    """
    if folders.sent and message.get("parentFolderId") == folders.sent:
        return False
    owner_address = matching.intended_owner(participants, internals.owner_by_email.keys())
    if owner_address is None:
        return False
    owner_user_id = internals.owner_by_email[owner_address]
    if owner_user_id == connection.user_id:
        return False
    return owner_user_id in internals.syncing_user_ids


# --------------------------------------------------------------------------- #
# Body fetch — after approval (or inline on auto-approve)
# --------------------------------------------------------------------------- #
async def fetch_body(session: AsyncSession, org: Org, interaction_id: uuid.UUID) -> bool:
    ctx = SystemContext(org=org, session=session)
    ref = await interactions_system.mailbox_email_ref(ctx, interaction_id)
    if ref is None or ref[2] != SOURCE:
        return False
    owner_user_id, message_id, _ = ref
    connection = await session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == org.id, MicrosoftConnection.user_id == owner_user_id
        )
    )
    if connection is None:
        return False
    try:
        async with acting_as(session, org, connection) as client:
            return await _fetch_body_with(client, ctx, interaction_id, message_id, owner_user_id)
    except Exception as exc:
        from app.integrations.microsoft.client import is_oauth_error

        if await is_oauth_error(exc):
            await mark_connection_error(session, org, connection, str(exc))
            return False
        raise


async def _fetch_body_with(
    client, ctx: SystemContext, interaction_id, message_id: str, owner_user_id
) -> bool:
    response = await client.get(
        f"/me/messages/{message_id}",
        params={"$select": "body,hasAttachments"},
        headers={"Prefer": BODY_PREFER},
    )
    if response.status_code == 404:
        return False
    response.raise_for_status()
    payload = response.json() or {}
    body = payload.get("body") or {}
    content = body.get("content") or ""
    # Two bodies, one message: the plain text search reads, and — only when the message
    # actually had an HTML part — the same words with their formatting kept.
    if (body.get("contentType") or "").lower() == "html":
        body_markdown = html_to_markdown(content)
        body_text = matching.html_to_text(content)
    else:
        body_markdown = None
        body_text = content.strip() or None
    if body_text is None:
        return False
    await interactions_system.set_body(ctx, interaction_id, body_text, body_markdown)
    if payload.get("hasAttachments", True):
        inline = await _store_attachments(
            client, ctx, interaction_id, message_id, owner_user_id, body_markdown
        )
        if inline and body_markdown:
            await interactions_system.set_body_markdown(
                ctx, interaction_id, rewrite_cid_images(body_markdown, inline)
            )
    return True


async def _store_attachments(
    client,
    ctx: SystemContext,
    interaction_id,
    message_id: str,
    owner_user_id,
    body_markdown: str | None = None,
) -> dict[str, str]:
    """Fetch and store the message's file attachments; returns ``{content id: file id}`` for
    the inline ones — the signature logos and pasted images the body points at. An item or a
    reference attachment (an Outlook item, a OneDrive link) carries no bytes and is skipped."""
    from app.core.storage import system as storage_system

    listing = await client.get(
        f"/me/messages/{message_id}/attachments",
        params={"$select": "id,name,contentType,size,isInline,contentId"},
    )
    if listing.status_code >= 400:
        logger.warning("outlook attachment listing failed for %s", interaction_id)
        return {}
    parts = [
        part
        for part in (listing.json() or {}).get("value") or []
        if part.get("id") and (part.get("@odata.type") or FILE_ATTACHMENT) == FILE_ATTACHMENT
    ]
    if not parts:
        return {}
    # The bodyless sweep may re-offer a fetch; the same attachments must not store twice.
    if await storage_system.entity_has_files(ctx, "interaction", interaction_id):
        return {}
    inline_cids = referenced_cids(body_markdown)
    resolved: dict[str, str] = {}
    for part in parts:
        content_id = (part.get("contentId") or "").strip().strip("<>") or None
        inline = content_id in inline_cids if content_id else False
        if not inline and not part.get("name"):
            continue
        response = await client.get(f"/me/messages/{message_id}/attachments/{part['id']}/$value")
        if response.status_code >= 400:
            logger.warning("outlook attachment fetch failed for %s", interaction_id)
            continue
        stored = await storage_system.store_system_file(
            ctx,
            filename=str(part.get("name") or content_id or "bijlage"),
            content_type=str(part.get("contentType") or "application/octet-stream"),
            data=response.content,
            entity_type="interaction",
            entity_id=interaction_id,
            content_id=content_id if inline else None,
            created_by_user_id=owner_user_id,
        )
        if stored is None:
            logger.info(
                "outlook attachment skipped (type/size) for %s: %s",
                interaction_id,
                part.get("name"),
            )
        elif inline and content_id:
            resolved[content_id] = str(stored.id)
    return resolved


# --------------------------------------------------------------------------- #
# Suppression (the interaction.rejected subscriber's write)
# --------------------------------------------------------------------------- #
async def suppress(
    session: AsyncSession,
    org_id: uuid.UUID,
    connection_id: uuid.UUID,
    *,
    message_id: str | None,
    conversation_id: str | None,
) -> None:
    if message_id and not await _suppressed(session, org_id, connection_id, message_id=message_id):
        session.add(
            OutlookSuppression(org_id=org_id, connection_id=connection_id, message_id=message_id)
        )
    if conversation_id:
        already = await session.scalar(
            select(OutlookSuppression.id)
            .where(
                OutlookSuppression.org_id == org_id,
                OutlookSuppression.connection_id == connection_id,
                OutlookSuppression.conversation_id == conversation_id,
                OutlookSuppression.message_id.is_(None),
            )
            .limit(1)
        )
        if already is None:
            session.add(
                OutlookSuppression(
                    org_id=org_id, connection_id=connection_id, conversation_id=conversation_id
                )
            )
    await session.flush()
