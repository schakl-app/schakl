"""The gmail ingest pipeline: historyId polling, matched metadata-first logging (GOOGLE.md §6).

Never a mailbox sync. Per poll, per connection: pull the message ids added since the stored
``historyId``, fetch **metadata only** (headers + snippet + labels), and log the ones whose
participants match a known contact — pending by default, so the mailbox owner approves before
any content is shared. Bodies are fetched separately, only after approval (or immediately when
the org runs ``auto_approve``).

**The skip chain is :func:`classify`, and it is a function rather than a shape.** It used to be
nine bare ``return 0``s interleaved with the fetch and the write, which made the decision
impossible to *ask for* without performing it — so "why did this email never appear?" had no
answer, for the mailbox owner or for us. Now this module fetches and acts, and every reason it
declines has a name (:mod:`~app.modules.google.gmail.gates`). The manual importer's explainer
calls the same function, which is what stops the explanation drifting from the behaviour.

First poll stores the current ``historyId`` and imports nothing — connecting a mailbox is
opt-in *going forward*, never a retroactive import.
"""

from __future__ import annotations

import base64
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import SystemContext
from app.core.htmlmd import referenced_cids, rewrite_cid_images
from app.core.models import Org
from app.core.portal import external_user_ids
from app.modules.google.client import acting_as, mark_connection_error
from app.modules.google.gmail import matching
from app.modules.google.gmail.gates import (
    PERSISTED_REASONS,
    Decision,
    GateCache,
    SkipReason,
    skip_row_values,
)
from app.modules.google.gmail.models import GmailSkip, GmailSuppression
from app.modules.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.modules.google.oauth import SCOPE_GMAIL, google_settings_row
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
            internals = await _internals(session, org.id)
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
                            internals,
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
                    # Loud in the log is not the same as findable. This is one of the two
                    # skips a person would never know to go looking for — the message simply
                    # is not there — so it leaves a row (id + reason + when, no content).
                    # Outside the rolled-back savepoint, or the record of the failure would be
                    # undone by the failure.
                    await _record_skip(
                        session,
                        org,
                        connection,
                        {"id": message_id},
                        SkipReason.INGEST_ERROR,
                        {"error": type(ingest_exc).__name__},
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


async def message_already_here(
    session: AsyncSession,
    org: Org,
    connection: GoogleConnection,
    message_id: str,
    cache: GateCache | None = None,
) -> SkipReason | None:
    """The two answers that need no message — asked before the fetch, and again inside
    :func:`classify`.

    Split out rather than inlined so the poller can decline without paying a Gmail round trip
    *and* the chain stays one function. Duplicating them here would be exactly the drift
    :mod:`~app.modules.google.gmail.gates` exists to prevent, one gate earlier.
    """
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
    connection: GoogleConnection,
    settings_row: GoogleSettings,
    message: dict,
    excluded_label_id: str | None,
    internals: Internals,
    cache: GateCache | None = None,
) -> Decision:
    """Would this message be logged, and if not, which gate stopped it?

    **Decides; never writes and never fetches.** That is the whole point: the poller called
    this chain by running it, so the only way to find out what it thought was to let it act,
    and "why did this email never appear?" had no answer anybody could give. Taking the fetched
    message as an argument is what lets the manual importer ask the same question about one
    message a person is looking at — the same function, so the explanation cannot drift from
    the behaviour (#324's rule, applied to the chain).

    The gates are **ordered and short-circuiting**, and the returned reason is the first one
    that fired. Anything after it was not evaluated, which is why this returns one reason
    rather than a list: "it also has no contact match" is not something we know about a message
    we stopped reading at the excluded label.

    ``cache`` is for a caller classifying a whole conversation in one request (:class:`GateCache`)
    — it changes where four of these lookups get their data, never what any of them asks. The
    poller passes none and every question goes to the database, one message at a time.
    """
    ctx = SystemContext(org=org, session=session)
    message_id = str(message.get("id") or "")

    already = await message_already_here(session, org, connection, message_id, cache)
    if already is not None:
        return Decision(reason=already)

    label_ids = message.get("labelIds") or []
    labels = set(label_ids)
    if labels & {"DRAFT", "SPAM", "TRASH"}:
        return Decision(reason=SkipReason.NOT_A_MESSAGE)
    if not matching.is_relevant(label_ids, excluded_label_id):
        # Split from the line above on purpose. ``is_relevant`` answers one question the poller
        # only ever needed one answer to; a person asking *why* needs the two apart, because
        # one of them is a label they chose and can un-choose, and the other is not a message.
        return Decision(
            reason=SkipReason.EXCLUDED_LABEL,
            detail={"label": connection.gmail_excluded_label or ""},
        )

    thread_id = message.get("threadId")
    if thread_id:
        if cache is not None and cache.suppressed_thread_ids is not None:
            suppressed_thread = thread_id in cache.suppressed_thread_ids
        else:
            suppressed_thread = await _suppressed(
                session, org.id, connection.id, thread_id=thread_id
            )
        if suppressed_thread:
            return Decision(reason=SkipReason.SUPPRESSED_THREAD)

    headers = matching.headers_map(message)
    rfc822_id = (headers.get("Message-ID") or "").strip()[:512] or None
    if rfc822_id:
        if cache is not None and cache.logged_rfc822_ids is not None:
            logged_elsewhere = rfc822_id in cache.logged_rfc822_ids
        else:
            logged_elsewhere = await interactions_system.rfc822_seen(ctx, rfc822_id)
        if logged_elsewhere:
            # A colleague's mailbox already logged this email — one timeline entry.
            return Decision(reason=SkipReason.LOGGED_ELSEWHERE)

    participants = matching.parse_participants(headers)
    if not participants:
        return Decision(reason=SkipReason.NO_PARTICIPANTS)
    if _defer_to_owner_mailbox(connection, label_ids, participants, internals):
        owner = matching.intended_owner(participants, internals.owner_by_email.keys())
        return Decision(
            reason=SkipReason.DEFERRED_TO_OWNER,
            detail={"owner": owner or ""},
        )

    internal = matching.internal_only(participants, internals.ours)
    if internal and not settings_row.gmail_log_internal:
        return Decision(reason=SkipReason.INTERNAL_ONLY)
    # The two-query lookup, memoised on the address set: one conversation is the same handful
    # of people over and over, so this collapses fifty pairs of queries into one or two.
    addresses = tuple(sorted({p["email"] for p in participants}))
    if cache is not None and addresses in cache.contacts_by_addresses:
        matches = cache.contacts_by_addresses[addresses]
    else:
        matches = await _match_contacts(session, org.id, participants, internals)
        if cache is not None:
            cache.contacts_by_addresses[addresses] = matches
    if not internal and not matching.has_external_match(matches, internals.company_ids):
        # A mail with an outsider on it still needs that outsider to be a contact we know —
        # and "known" has to mean known *and outside* (#324). Our own staff hold contact rows
        # too, which is the setup ``_internals`` assumes and derives ``company_ids`` from, so
        # "matched anything" let every newsletter, supplier invoice, password reset and GitHub
        # notification addressed to a colleague straight through: it matched the colleague,
        # and the row landed in their review queue filed on the agency's own company.
        # ``gmail_log_internal`` remains the only door for a message with nobody outside on it.
        return Decision(reason=SkipReason.NO_EXTERNAL_MATCH)

    if not thread_id:
        inherited = None
    elif cache is not None and thread_id in cache.mappings_by_thread:
        inherited = cache.mappings_by_thread[thread_id]
    else:
        inherited = await interactions_system.thread_mappings(ctx, thread_id)
        if cache is not None:
            cache.mappings_by_thread[thread_id] = inherited
    mappings = (
        dict(inherited)
        if inherited
        else matching.resolve_mappings(
            matches, internal_company_ids=internals.company_ids
        )
    )
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
    return Decision(mappings=mappings, pending=pending)


async def _ingest_message(
    session: AsyncSession,
    org: Org,
    connection: GoogleConnection,
    settings_row: GoogleSettings,
    client,
    message_id: str,
    excluded_label_id: str | None,
    internals: Internals,
) -> int:
    """Fetch one message, ask :func:`classify` what to do with it, and do that.

    Three steps that used to be one, and the middle one is the whole change: what this
    function knows about the rules is now *nothing*. It fetches, it acts, and it says out loud
    which gate declined — eight of the nine skips were silent returns, so a mailbox owner's
    "this email never appeared" had no counterpart anywhere in the logs.
    """
    ctx = SystemContext(org=org, session=session)
    # Before the fetch: the two answers that need no message. A poll re-reads its whole history
    # page, so paying a Gmail round trip per already-seen id would be the expensive way to
    # learn nothing.
    early = await message_already_here(session, org, connection, message_id)
    if early is not None:
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
    decision = await classify(
        session, org, connection, settings_row, message, excluded_label_id, internals
    )
    if not decision.logs:
        await _record_skip(session, org, connection, message, decision.reason, decision.detail)
        logger.debug(
            "Gmail skipped message %s on %s (org %s): %s",
            message_id,
            connection.email,
            org.id,
            decision.reason,
        )
        return 0

    label_ids = message.get("labelIds") or []
    thread_id = message.get("threadId")
    headers = matching.headers_map(message)
    rfc822_id = (headers.get("Message-ID") or "").strip()[:512] or None
    participants = matching.parse_participants(headers)
    mappings, pending = decision.mappings, decision.pending

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
        snippet=matching.clean_snippet(message.get("snippet")),
        direction=matching.direction_of(
            label_ids,
            sender_internal=matching.sender_of(participants) in internals.owner_by_email,
        ),
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


#: How long a ``gmail_skips`` row is kept. Long enough that "an email from last month never
#: arrived" is still answerable; short enough that the table cannot become a history of the
#: mailbox, which is the thing :mod:`gates` refuses to build.
SKIP_RETENTION_DAYS = 60


async def reap_skips(org: Org, session: AsyncSession) -> None:
    """Drop this org's expired skip rows — the retention half of storing any at all."""
    cutoff = datetime.now(UTC) - timedelta(days=SKIP_RETENTION_DAYS)
    await session.execute(
        delete(GmailSkip).where(GmailSkip.org_id == org.id, GmailSkip.created_at < cutoff)
    )


async def _record_skip(
    session: AsyncSession,
    org: Org,
    connection: GoogleConnection,
    message: dict,
    reason: SkipReason | None,
    detail: dict[str, str],
) -> None:
    """Persist the two skips a person would never know to go looking for.

    Deliberately **not** every skip. A row per decision is a record of every email the mailbox
    receives, which is more than this module is allowed to know (:mod:`gates`); these two are
    failures rather than policy, are rare enough to have no volume, and are invisible by
    construction — a deferral to a mailbox that later stops polling loses the email outright,
    and a poison message is skipped precisely so that it cannot wedge the feed.

    Upserted on the natural key, so a message re-offered on every poll leaves one row and the
    reaper's retention window means what it says.
    """
    if reason not in PERSISTED_REASONS:
        return
    values = skip_row_values(
        reason,
        message_id=str(message.get("id") or ""),
        thread_id=message.get("threadId"),
        detail=detail,
    )
    if not values["gmail_message_id"]:
        return
    await session.execute(
        pg_insert(GmailSkip)
        .values(org_id=org.id, connection_id=connection.id, **values)
        .on_conflict_do_update(
            index_elements=[
                GmailSkip.org_id,
                GmailSkip.connection_id,
                GmailSkip.gmail_message_id,
            ],
            set_={"reason": values["reason"], "detail": values["detail"]},
        )
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


@dataclass(frozen=True)
class Internals:
    """Who counts as *us*, resolved once per poll rather than once per message.

    ``ours`` and ``company_ids`` answer one question — "is this person the agency, or somebody
    outside it?" — which the feed asks three times: to skip colleague-only chatter, to decide
    whether the message is CRM-relevant at all (#324), and to rank the mapping (#305).
    ``member_emails`` exists to derive the company half. ``owner_by_email`` and
    ``syncing_user_ids`` answer a different question: *which* colleague, and does their own
    mailbox poll.
    """

    #: Staff *login* addresses (``users.email``). Narrow on purpose: it is what derives
    #: ``company_ids``, and a colleague who connected a private Google account must never make
    #: whichever company that address is a contact of read as the agency's own.
    member_emails: frozenset[str]
    #: Companies that are the agency itself rather than one of its clients.
    company_ids: frozenset[uuid.UUID]
    #: Every address that reaches a colleague → their user id. Wider than ``member_emails`` on
    #: purpose: it also carries the address each Google grant was made with, because what has
    #: to be found here is a *mailbox*, and someone whose ``users.email`` differs from their
    #: Workspace address would otherwise resolve to nobody.
    owner_by_email: dict[str, uuid.UUID] = field(default_factory=dict)
    #: Users whose own mailbox is genuinely being polled: active, opted in, holding the Gmail
    #: scope. Deferring to a mailbox that will never poll would lose the email outright.
    syncing_user_ids: frozenset[uuid.UUID] = frozenset()

    @property
    def ours(self) -> frozenset[str]:
        """Every address that is one of ours — the keys of ``owner_by_email``.

        One set answers "is this person outside the agency?" wherever it is asked: whether the
        message is colleague-to-colleague chatter (``internal_only``) and whether a matched
        contact row is a colleague's (``is_staff``). Two sets is how a mail to somebody's
        Workspace alias came out *external* on one question and *internal* on the other (#324);
        the alias reaches the same person either way.
        """
        return frozenset(self.owner_by_email)


async def _internals(session: AsyncSession, org_id: uuid.UUID) -> Internals:
    """The staff addresses, and the companies they are the contacts of.

    A client login is an ordinary membership — so a naive all-memberships set makes every
    invited client look like a colleague, and ``internal_only`` then silently drops their
    entire correspondence (polls succeed, ``logged:0`` forever). Client logins are excluded
    through the core seam; they keep matching as *contacts*, which is what they are.

    The seam has to be asked the **whole** question (:func:`external_user_ids`, #274). This
    used to ask ``portal_user_ids`` — "is this user contact-linked?" — which is only half of
    what an external login is: a client invited straight from Instellingen → Gebruikers holds
    the ``client`` role and no contact link, so they landed in ``member_emails`` and every mail
    they wrote to a colleague read as colleague-to-colleague chatter and was dropped. The
    second-order damage was worse than the first: their address then made *their own company*
    read as the agency's own (the ``company_ids`` derivation below), so since #324's gate every
    other contact at that client was dropped too. Nothing appears anywhere — no pending row, no
    notification, no log line — which is exactly why this asks core rather than restating it.

    The company half is **derived, never configured**: an agency that keeps its own company in
    its own list — the ordinary thing to do, and what invoicing and its own domains want — has
    staff on it as contacts, and no other company does. So "a company whose contact is a
    colleague" identifies it without asking anyone to set a flag they would forget, and it
    stays right when a second entity is added later. Nothing is hidden on the strength of it:
    it only ranks a company below a genuine client (``resolve_mappings``), so the failure mode
    of a staff member who really is a contact at a client is one email filed where a reviewer
    would have filed it anyway.
    """
    rows = await session.execute(
        text(
            "SELECT u.id, lower(u.email) FROM users u "
            "JOIN memberships m ON m.user_id = u.id WHERE m.org_id = :oid"
        ),
        {"oid": org_id},
    )
    pairs = [(row[0], row[1]) for row in rows]
    external = await external_user_ids(session, org_id, {uid for uid, _ in pairs})
    member_emails = frozenset(email for uid, email in pairs if uid not in external)
    # The mailboxes that actually poll, and the addresses that reach them. Same predicate the
    # cron offers on (``jobs.google_gmail_poll``) — stated once there and once here is already
    # one copy too many, but the alternative is the cron importing this module to ask.
    connection_rows = await session.execute(
        text(
            "SELECT user_id, lower(email), status, gmail_sync_enabled, scopes "
            "FROM google_connections WHERE org_id = :oid ORDER BY created_at"
        ),
        {"oid": org_id},
    )
    owner_by_email = {email: uid for uid, email in pairs if uid not in external}
    syncing: set[uuid.UUID] = set()
    for user_id, email, status, sync_enabled, scopes in connection_rows:
        if user_id in external:
            continue
        owner_by_email.setdefault(email, user_id)
        if (
            status == ConnectionStatus.ACTIVE.value
            and sync_enabled
            and SCOPE_GMAIL in (scopes or [])
        ):
            syncing.add(user_id)
    syncing_user_ids = frozenset(syncing)
    if not member_emails:
        return Internals(
            member_emails=member_emails,
            company_ids=frozenset(),
            owner_by_email=owner_by_email,
            syncing_user_ids=syncing_user_ids,
        )
    company_rows = await session.execute(
        text(
            "SELECT DISTINCT cc.company_id FROM company_contacts cc "
            "JOIN contacts c ON c.id = cc.contact_id AND c.org_id = cc.org_id "
            "WHERE cc.org_id = :oid AND lower(c.email) = ANY(:emails)"
        ),
        {"oid": org_id, "emails": sorted(member_emails)},
    )
    return Internals(
        member_emails=member_emails,
        company_ids=frozenset(row[0] for row in company_rows),
        owner_by_email=owner_by_email,
        syncing_user_ids=syncing_user_ids,
    )


def _defer_to_owner_mailbox(
    connection: GoogleConnection,
    label_ids: list[str],
    participants: list[dict[str, str]],
    internals: Internals,
) -> bool:
    """Is this copy somebody else's, and will their own mailbox log it?

    One email, several colleagues, one row (the RFC-822 dedup): whoever polled first won, so
    an ``info@`` address Bcc'd on the agency's outgoing mail claimed every one of them. The
    row then named the wrong person as owner *and* read as inbound, and — because a pending
    row is private to its owner with no admin escape (``interactions`` §15) — the person who
    wrote the mail could not see it at all. That is what "the email never arrived" was.

    So a mailbox holding a copy it is not the subject of stands aside and lets the intended
    owner's mailbox log its own copy. Deferring rather than re-stamping the owner is what
    keeps the row coherent: ``gmail_message_id`` is only meaningful inside the mailbox it came
    from, so the owner's copy is the only one whose deep link opens in *their* Gmail and whose
    body fetch uses their own grant.

    Never deferred: a copy carrying ``SENT`` (a mailbox does not give away its own outgoing
    mail, whatever the headers say), a message naming no colleague at all, and — the load-
    bearing one — anything whose intended owner is not in ``syncing_user_ids``. Standing
    aside for a mailbox that is disconnected, opted out or missing the Gmail scope would drop
    the email entirely, so in that case this copy is the only one there will ever be and it
    logs here, with its direction read from the headers rather than from the missing label.
    """
    if "SENT" in label_ids:
        return False
    owner_address = matching.intended_owner(participants, internals.owner_by_email.keys())
    if owner_address is None:
        return False
    owner_user_id = internals.owner_by_email[owner_address]
    if owner_user_id == connection.user_id:
        return False
    return owner_user_id in internals.syncing_user_ids


async def _match_contacts(
    session: AsyncSession,
    org_id: uuid.UUID,
    participants: list[dict[str, str]],
    internals: Internals,
) -> list[matching.ContactMatch]:
    """Participant addresses → contacts (+ their companies, oldest link first), each carrying
    the header it was found on and whether it is a colleague — which is what the ingest gate
    filters on and ``resolve_mappings`` ranks by. Bare-table lookups, never a contacts-module
    import (§6)."""
    # First occurrence wins: participants read From, To, Cc, so this is the most central header
    # each address appears on.
    roles: dict[str, str] = {}
    for participant in participants:
        roles.setdefault(participant["email"], participant["role"])
    if not roles:
        return []
    addresses = sorted(roles)
    contact_rows = await session.execute(
        text(
            "SELECT id, lower(email) FROM contacts "
            "WHERE org_id = :oid AND lower(email) = ANY(:addrs) ORDER BY created_at"
        ),
        {"oid": org_id, "addrs": addresses},
    )
    found = [(row[0], row[1]) for row in contact_rows]
    if not found:
        return []
    # One query for every match's companies — per-contact would be N+1 in the poll loop.
    link_rows = await session.execute(
        text(
            "SELECT contact_id, company_id FROM company_contacts "
            "WHERE org_id = :oid AND contact_id = ANY(:cids) ORDER BY created_at"
        ),
        {"oid": org_id, "cids": [contact_id for contact_id, _ in found]},
    )
    companies: dict[uuid.UUID, list[uuid.UUID]] = {}
    for contact_id, company_id in link_rows:
        companies.setdefault(contact_id, []).append(company_id)
    # ``ours``, not ``member_emails``: a contact row on a colleague's Workspace alias is still
    # a colleague's, and now that the gate reads this flag, calling it an outsider is what
    # would let the newsletter back in (#324). Resolved once, not once per match.
    ours = internals.ours
    return [
        matching.ContactMatch(
            contact_id=contact_id,
            company_ids=companies.get(contact_id, []),
            role=roles.get(email, "to"),
            is_staff=email in ours,
        )
        for contact_id, email in found
    ]


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
    # Two bodies, one message: the plain text search reads, and — only when the message
    # actually had an HTML part — the same words with their formatting kept.
    body_markdown = matching.extract_markdown(payload)
    await interactions_system.set_body(ctx, interaction_id, body_text, body_markdown)
    # Attachments ride the same approval-time fetch (#180): the full payload already names
    # them, so this is one extra call per attachment, never per message. A pending row never
    # reaches this code — reject must leave no stored bytes anywhere.
    inline = await _store_attachments(
        client, ctx, interaction_id, message_id, payload, owner_user_id, body_markdown
    )
    if inline and body_markdown:
        # The body's `cid:` markers now name stored files. Rewriting keeps the stored body
        # self-contained; a part we could not store degrades to its alt text.
        await interactions_system.set_body_markdown(
            ctx, interaction_id, rewrite_cid_images(body_markdown, inline)
        )
    return True


async def _store_attachments(
    client,
    ctx: SystemContext,
    interaction_id,
    message_id: str,
    payload: dict,
    owner_user_id,
    body_markdown: str | None = None,
) -> dict[str, str]:
    """Fetch and store the message's parts; returns ``{content id: file id}`` for the inline
    ones — the signature logos and pasted images the body points at.

    An inline part is stored like any other (and de-duplicated like any other, which is what
    makes the same logo on every message from a sender cost one object — docs/STORAGE.md), but
    it is marked ``content_id`` so it renders *in* the body instead of as an attachment chip on
    every mail the sender ever sent.
    """
    from app.core.storage import system as storage_system

    parts = matching.attachment_parts(payload)
    if not parts:
        return {}
    # The bodyless sweep may re-offer a fetch; the same attachments must not store twice.
    if await storage_system.entity_has_files(ctx, "interaction", interaction_id):
        return {}
    # What the converted body actually references is the only honest test of "inline": a part
    # with a Content-ID nothing points at is an ordinary attachment, whatever it was labelled.
    inline_cids = referenced_cids(body_markdown)
    resolved: dict[str, str] = {}
    for part in parts:
        attachment_id = (part.get("body") or {}).get("attachmentId")
        content_id = matching.part_content_id(part)
        inline = content_id in inline_cids if content_id else False
        if not inline and not part.get("filename"):
            continue
        response = await client.get(
            f"{GMAIL_API}/messages/{message_id}/attachments/{attachment_id}"
        )
        if response.status_code >= 400:
            logger.warning("gmail attachment fetch failed for %s", interaction_id)
            continue
        data = base64.urlsafe_b64decode(response.json().get("data") or "")
        stored = await storage_system.store_system_file(
            ctx,
            filename=str(part.get("filename") or content_id or "bijlage"),
            content_type=str(part.get("mimeType") or "application/octet-stream"),
            data=data,
            entity_type="interaction",
            entity_id=interaction_id,
            content_id=content_id if inline else None,
            created_by_user_id=owner_user_id,
        )
        if stored is None:
            # Type/size validation skipped it — worth a log line, never a failed body fetch.
            logger.info(
                "gmail attachment skipped (type/size) for %s: %s",
                interaction_id,
                part.get("filename"),
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
