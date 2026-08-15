"""Pulling an email out of Gmail by hand, when the poller decided not to log it (#342).

`_ingest_message` has **nine** silent exits, and the two that hurt most are ordinary: a message
from somebody who is not a contact yet (`has_external_match`), and anything older than the day
the mailbox was connected — the first poll baselines and imports nothing, on purpose. Add an
expired `historyId` (Gmail keeps about a week), a deferral to a colleague's mailbox that later
opted out, an excluded label, an earlier rejection, and "why is this email not on the timeline?"
has no answer anybody can act on.

The thing worth noticing is that **almost none of those are blindness — they are decisions.**
The message is sitting in a mailbox we already hold a grant for. So this module does exactly
one thing: it lets the person who owns that mailbox name a message and override the decision.

Four rules hold it up.

**The id space is Google's, so the guard is Google's.** Every call here goes through
`acting_as(session, org, connection)` — the *caller's own* OAuth grant — and Gmail message ids
are meaningful only inside one mailbox. `messages.get` with your token cannot return a message
from a colleague's mailbox; a guessed, copied or brute-forced id answers 404. That is what makes
"accept an id from the client" safe here and unsafe almost everywhere else: it is not an id into
*our* tables, where the check would be ours to get right. We gain no reach the poller did not
already have, which is also why the answer to "should this be a mailbox browser?" is no — a
picker means `messages.list` over arbitrary personal mail inside the CRM, which is the trust
landmine docs/GOOGLE.md names, and it would make "schakl only ever sees matched mail" untrue.

**A thread we already logged is not new reach.** The commonest complaint is not "this email"
but "the *rest* of this conversation" — a reply that never arrived, or the first message of a
thread whose later ones are all here. We already hold that `gmail_thread_id`, legitimately, so
`thread_messages` reads exactly one thread and marks which of its messages are already on the
timeline. No search, no browsing, no new consent, and it is the answer to the id problem below.

**Gmail's web ids are not Gmail's API ids, and pretending otherwise fails silently.** What a
person copies out of the address bar today is usually an opaque `FMfcgz…` thread id from the
web UI, which the API does not accept and cannot convert. Three references *do* resolve, and
this module accepts exactly those: a hex id (what the API uses, and what older links and
`&th=` carry), a `msg-f:`/`thread-f:` decimal (the same id in the other base), and the RFC-822
`Message-ID` from "Toon origineel", looked up with `q=rfc822msgid:` — a single-purpose search
that is not a mailbox search. Anything else is refused with a message that names the two things
that work, because an unresolvable link answered with a generic failure is how somebody
concludes the feature is broken.

**A refusal to guess.** Nothing here matches contacts, ranks companies or infers a client: the
caller says where the message is filed, exactly as an uploaded ``.eml`` does. Every one of the
matching rules that could have run is a rule that already declined this message once.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.auth.ratelimit import limit_by_principal
from app.core.events import SystemContext
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.google.client import acting_as, is_oauth_error, mark_connection_error
from app.modules.google.gmail import matching
from app.modules.google.gmail.models import GmailSuppression
from app.modules.google.gmail.service import (
    GMAIL_API,
    _fetch_body_with,
    _internals,
    deep_link,
)
from app.modules.google.models import ConnectionStatus, GoogleConnection
from app.modules.google.oauth import SCOPE_GMAIL, google_settings_row
from app.modules.interactions import system as interactions_system

logger = logging.getLogger("schakl.google.gmail")

#: Headers we ask for. Wider than the poller's set by one — ``Date`` — because a manual import
#: has no ``internalDate`` fallback worth trusting when the message is old.
_HEADERS = ("From", "To", "Cc", "Subject", "Message-ID", "Date")

#: A Gmail API id: hex, and long enough not to match a stray word someone pasted.
_HEX_ID = re.compile(r"^[0-9a-f]{8,32}$", re.IGNORECASE)
#: ``msg-f:1234…`` / ``thread-f:1234…`` — the same id in decimal, as some Gmail links spell it.
_DECIMAL_ID = re.compile(r"^(?:msg|thread)-[af]:(\d{6,25})$", re.IGNORECASE)
#: An RFC 5322 ``Message-ID``, with or without its angle brackets.
_RFC822_ID = re.compile(r"^<?([^\s<>@]+@[^\s<>@]+)>?$")
#: How many messages of one thread we will describe. A conversation is not a mailbox.
MAX_THREAD_MESSAGES = 50


class GmailCandidate(BaseModel):
    """One message the caller could log, and everything the row needs to describe itself."""

    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    #: Everyone else on it, as a display string — the screen names who it was with.
    recipients: str | None = None
    occurred_at: datetime | None = None
    snippet: str | None = None
    direction: str = "none"
    #: Already on the timeline. ``interaction_id`` names the row, so the screen can link to it
    #: instead of offering a button that will only ever answer "al vastgelegd".
    logged: bool = False
    interaction_id: uuid.UUID | None = None
    #: This mailbox rejected it (or its thread) earlier. Importing anyway is allowed — it is
    #: the caller's own earlier decision — but a screen that does not say so is a trap.
    suppressed: bool = False


class GmailLookupResult(BaseModel):
    #: The thread these messages belong to, when the reference resolved to one.
    thread_id: str | None = None
    messages: list[GmailCandidate] = Field(default_factory=list)
    #: Gmail had more messages in this thread than we describe (``MAX_THREAD_MESSAGES``).
    truncated: bool = False


class GmailImportResult(BaseModel):
    interaction_id: uuid.UUID
    subject: str | None = None
    #: Whether the body (and its attachments) landed in the same request. ``False`` is not a
    #: failure: the row is its own outbox and the five-minute sweep re-tries it.
    body_fetched: bool = False


# --------------------------------------------------------------------------- #
# What the caller pasted
# --------------------------------------------------------------------------- #


class Reference(BaseModel):
    """A parsed reference: ``kind`` is how to look it up, ``value`` is what to look up."""

    kind: str  # "id" | "rfc822"
    value: str


def parse_reference(raw: str) -> Reference:
    """Turn whatever was pasted into something the Gmail API will answer.

    Accepts a bare id, a ``msg-f:``/``thread-f:`` decimal, an RFC-822 ``Message-ID``, or a
    Gmail URL carrying any of those — in the fragment (``#all/<id>``, and the *last* segment of
    it, because an opened message reads ``#inbox/<thread>/<message>``) or in the ``th``/
    ``msgid`` query parameters older links use.

    Raises rather than guessing. An opaque web id is the common case and gets its own error
    key, because "we could not read that link" and "that link cannot be read by anyone" are
    different sentences and only the second one tells you what to do instead.
    """
    text = (raw or "").strip()
    if not text:
        raise AppError(
            "validation", "errors.gmail_reference_unreadable", status_code=422,
            fields={"reference": "errors.required"},
        )

    candidate = text
    if "mail.google.com" in text.lower():
        parsed = urlparse(text)
        fragment = unquote(parsed.fragment or "")
        # `th=` / `msgid=` on the older permalinks; the fragment on everything since.
        query = dict(
            part.split("=", 1)
            for part in (parsed.query or "").split("&")
            if "=" in part
        )
        candidate = (
            unquote(query.get("th") or query.get("msgid") or "")
            or (fragment.split("?")[0].rstrip("/").split("/")[-1] if fragment else "")
        )
        if not candidate:
            raise AppError(
                "validation", "errors.gmail_reference_web_id", status_code=422,
                fields={"reference": "errors.gmail_reference_web_id"},
            )

    decimal = _DECIMAL_ID.match(candidate)
    if decimal:
        # Same id, other base. Gmail's own links mix the two freely.
        return Reference(kind="id", value=format(int(decimal.group(1)), "x"))
    if _HEX_ID.match(candidate):
        return Reference(kind="id", value=candidate.lower())
    rfc822 = _RFC822_ID.match(candidate)
    if rfc822:
        return Reference(kind="rfc822", value=rfc822.group(1))
    # Anything left that came out of a Gmail URL is the opaque web id: name it, so the answer
    # is "use the Message-ID or open the conversation" rather than "that did not work".
    key = (
        "errors.gmail_reference_web_id"
        if "mail.google.com" in text.lower()
        else "errors.gmail_reference_unreadable"
    )
    raise AppError("validation", key, status_code=422, fields={"reference": key})


# --------------------------------------------------------------------------- #
# The caller's own mailbox
# --------------------------------------------------------------------------- #


async def _my_connection(ctx: RequestContext) -> GoogleConnection:
    """The caller's connection, or the reason there is nothing to read."""
    row = await google_settings_row(ctx.session, ctx.org.id)
    if row is None or not row.gmail_enabled:
        raise AppError("gmail_disabled", "errors.gmail_disabled", status_code=409)
    connection = await ctx.session.scalar(
        select(GoogleConnection).where(
            GoogleConnection.org_id == ctx.org.id,
            GoogleConnection.user_id == ctx.user.id,
        )
    )
    if connection is None:
        raise AppError(
            "google_not_connected", "errors.google_not_connected", status_code=409
        )
    if SCOPE_GMAIL not in (connection.scopes or []):
        # Note this does *not* ask for ``gmail_sync_enabled``. Opting your mailbox into
        # automatic logging and reaching into it for one named message are different consents,
        # and someone who deliberately keeps the feed off may still want to file one email.
        raise AppError("gmail_sync_off", "errors.gmail_sync_off", status_code=409)
    if connection.status != ConnectionStatus.ACTIVE.value:
        raise AppError(
            "google_connection_error", "errors.google_connection_error", status_code=409
        )
    return connection


async def _guard(ctx: RequestContext, *, bucket: str, limit: int) -> None:
    """Both keys, then the ceiling.

    The route declares one permission and the other is asked for here (#310): reading your own
    mailbox through our API is ``google.connection.manage``, and turning what comes back into a
    contactmoment is ``interactions.interaction.write``. Neither alone is this act, and a
    control gated on the key its own screen names is how a 403 becomes unexplainable.
    """
    ctx.require("google.connection.manage")
    ctx.require("interactions.interaction.write")
    await limit_by_principal(
        bucket=bucket, principal=f"{ctx.org.id}:{ctx.user.id}", limit=limit
    )


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #


def _candidate(message: dict, *, ours: frozenset[str]) -> GmailCandidate:
    headers = matching.headers_map(message)
    participants = matching.parse_participants(headers)
    sender = matching.sender_of(participants)
    label_ids = message.get("labelIds") or []
    internal_date = message.get("internalDate")
    occurred_at = (
        datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC) if internal_date else None
    )
    from_participant = next((p for p in participants if p.get("role") == "from"), None)
    others = [p for p in participants if p.get("role") != "from"]
    return GmailCandidate(
        message_id=str(message.get("id") or ""),
        thread_id=message.get("threadId"),
        subject=headers.get("Subject") or None,
        from_email=sender,
        from_name=(from_participant or {}).get("name") or None,
        recipients=", ".join(p["email"] for p in others[:5]) or None,
        occurred_at=occurred_at,
        snippet=matching.clean_snippet(message.get("snippet")),
        direction=matching.direction_of(label_ids, sender_internal=sender in ours),
    )


async def _decorate(
    ctx: RequestContext,
    connection: GoogleConnection,
    candidates: list[GmailCandidate],
    rfc822_by_message: dict[str, str],
) -> list[GmailCandidate]:
    """Mark what is already logged and what this mailbox rejected earlier — two queries, flat."""
    if not candidates:
        return candidates
    sys_ctx = SystemContext(org=ctx.org, session=ctx.session)
    message_ids = [c.message_id for c in candidates if c.message_id]
    logged = await interactions_system.logged_state_for_messages(
        sys_ctx,
        connection.user_id,
        message_ids,
        list(rfc822_by_message.values()),
    )
    thread_ids = {c.thread_id for c in candidates if c.thread_id}
    suppressed_rows = (
        (
            await ctx.session.execute(
                select(
                    GmailSuppression.gmail_message_id, GmailSuppression.gmail_thread_id
                ).where(
                    GmailSuppression.org_id == ctx.org.id,
                    GmailSuppression.connection_id == connection.id,
                )
            )
        )
        .all()
    )
    suppressed_messages = {row[0] for row in suppressed_rows if row[0]}
    suppressed_threads = {row[1] for row in suppressed_rows if row[1]} & thread_ids
    for candidate in candidates:
        row_id = logged.get(candidate.message_id) or logged.get(
            rfc822_by_message.get(candidate.message_id, "")
        )
        candidate.logged = row_id is not None
        candidate.interaction_id = row_id
        candidate.suppressed = (
            candidate.message_id in suppressed_messages
            or (candidate.thread_id in suppressed_threads)
        )
    return candidates


async def _handle_google_error(
    ctx: RequestContext, connection: GoogleConnection, exc: Exception
) -> None:
    if await is_oauth_error(exc):
        await mark_connection_error(ctx.session, ctx.org, connection, str(exc))
        raise AppError(
            "google_connection_error", "errors.google_connection_error", status_code=409
        ) from exc
    logger.exception("Gmail manual read failed for connection %s", connection.id)
    raise AppError("gmail_unavailable", "errors.gmail_unavailable", status_code=502) from exc


async def _get_metadata(client, message_id: str) -> dict | None:
    response = await client.get(
        f"{GMAIL_API}/messages/{message_id}",
        params={"format": "metadata", "metadataHeaders": list(_HEADERS)},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def _thread_payload(client, thread_id: str) -> dict | None:
    response = await client.get(
        f"{GMAIL_API}/threads/{thread_id}",
        params={"format": "metadata", "metadataHeaders": list(_HEADERS)},
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def _by_rfc822(client, rfc822_id: str) -> list[str]:
    """The one search this module makes, and it is not a mailbox search.

    ``rfc822msgid:`` names exactly one message — it is a lookup wearing a search's clothes, and
    it is the only reference a person can always obtain (Gmail → "Toon origineel").
    """
    response = await client.get(
        f"{GMAIL_API}/messages",
        params={"q": f"rfc822msgid:{rfc822_id}", "maxResults": "5"},
    )
    response.raise_for_status()
    return [m["id"] for m in (response.json().get("messages") or []) if m.get("id")]


def _build(
    messages: list[dict], ours: frozenset[str]
) -> tuple[list[GmailCandidate], dict[str, str]]:
    """The candidates, plus each one's RFC-822 id — kept beside the list rather than on it.

    The global ``Message-ID`` is how the *cross-mailbox* half of "is this already logged?" is
    asked, and it is read off headers we have already fetched. It is not on ``GmailCandidate``
    because the screen has no use for it, and a field that exists only to be ignored by every
    renderer is a field somebody eventually shows a client.
    """
    built: list[GmailCandidate] = []
    rfc822_by_message: dict[str, str] = {}
    for message in messages:
        candidate = _candidate(message, ours=ours)
        rfc822 = (matching.headers_map(message).get("Message-ID") or "").strip()[:512]
        if rfc822 and candidate.message_id:
            rfc822_by_message[candidate.message_id] = rfc822
        built.append(candidate)
    built.sort(key=lambda c: c.occurred_at or datetime.min.replace(tzinfo=UTC))
    return built, rfc822_by_message


async def lookup(ctx: RequestContext, reference: str) -> GmailLookupResult:
    """Resolve a pasted reference to the message(s) it names."""
    await _guard(
        ctx,
        bucket="gmail_manual_lookup",
        limit=settings.gmail_manual_lookup_rate_limit_per_minute,
    )
    parsed = parse_reference(reference)
    connection = await _my_connection(ctx)
    internals = await _internals(ctx.session, ctx.org.id)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            if parsed.kind == "rfc822":
                ids = await _by_rfc822(client, parsed.value)
                messages = [m for m in [await _get_metadata(client, i) for i in ids] if m]
            else:
                one = await _get_metadata(client, parsed.value)
                if one is not None:
                    messages = [one]
                else:
                    # Not a message id — the same id is very often the *thread's*, which is
                    # what a link off a conversation carries. One extra call, and it turns the
                    # most common paste into the gap-fill list rather than a "not found".
                    thread = await _thread_payload(client, parsed.value)
                    messages = (thread or {}).get("messages") or []
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — reported as a state, never as a 500
        await _handle_google_error(ctx, connection, exc)
        raise  # unreachable; _handle_google_error always raises

    if not messages:
        raise AppError("not_found", "errors.gmail_message_not_found", status_code=404)
    truncated = len(messages) > MAX_THREAD_MESSAGES
    built, rfc822_ids = _build(messages[:MAX_THREAD_MESSAGES], internals.ours)
    await _decorate(ctx, connection, built, rfc822_ids)
    return GmailLookupResult(
        thread_id=next((c.thread_id for c in built if c.thread_id), None),
        messages=built,
        truncated=truncated,
    )


async def thread_messages(ctx: RequestContext, thread_id: str) -> GmailLookupResult:
    """Every message of one thread, marked with what is already on the timeline.

    The reach question answers itself: the thread id came off a row we logged, so this reads a
    conversation we were already told about. It is the fix for the id problem too — the one
    reference nobody can paste is exactly the one this does not need.
    """
    await _guard(
        ctx,
        bucket="gmail_manual_lookup",
        limit=settings.gmail_manual_lookup_rate_limit_per_minute,
    )
    connection = await _my_connection(ctx)
    internals = await _internals(ctx.session, ctx.org.id)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            thread = await _thread_payload(client, thread_id)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_google_error(ctx, connection, exc)
        raise

    messages = (thread or {}).get("messages") or []
    if not messages:
        raise AppError("not_found", "errors.gmail_message_not_found", status_code=404)
    truncated = len(messages) > MAX_THREAD_MESSAGES
    built, rfc822_ids = _build(messages[:MAX_THREAD_MESSAGES], internals.ours)
    await _decorate(ctx, connection, built, rfc822_ids)
    return GmailLookupResult(thread_id=thread_id, messages=built, truncated=truncated)


# --------------------------------------------------------------------------- #
# Writing
# --------------------------------------------------------------------------- #


async def import_message(
    ctx: RequestContext,
    *,
    message_id: str,
    links: dict,
    enrich_task: bool = False,
    allow_duplicate: bool = False,
) -> GmailImportResult:
    """Log one named message, then fetch its body the way an approval does."""
    await _guard(
        ctx,
        bucket="gmail_manual_import",
        limit=settings.gmail_manual_import_rate_limit_per_minute,
    )
    connection = await _my_connection(ctx)
    internals = await _internals(ctx.session, ctx.org.id)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            message = await _get_metadata(client, message_id)
            if message is None:
                raise AppError(
                    "not_found", "errors.gmail_message_not_found", status_code=404
                )
            headers = matching.headers_map(message)
            participants = matching.parse_participants(headers)
            if not participants:
                raise AppError(
                    "validation", "errors.gmail_message_unusable", status_code=422
                )
            sender = matching.sender_of(participants)
            internal_date = message.get("internalDate")
            row = await interactions_system.record_manual_gmail_email(
                ctx,
                owner_user_id=connection.user_id,
                owner_name=ctx.user.full_name or ctx.user.email,
                occurred_at=(
                    datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC)
                    if internal_date
                    else datetime.now(UTC)
                ),
                subject=headers.get("Subject") or None,
                snippet=matching.clean_snippet(message.get("snippet")),
                direction=matching.direction_of(
                    message.get("labelIds") or [], sender_internal=sender in internals.ours
                ),
                participants=participants,
                gmail_message_id=message_id,
                gmail_thread_id=message.get("threadId"),
                rfc822_message_id=(headers.get("Message-ID") or "").strip()[:512] or None,
                deep_link=deep_link(message_id),
                links=links,
                allow_duplicate=allow_duplicate,
                enrich_task=enrich_task,
            )
            # The body in the same request, because a person is waiting and already decided
            # this message belongs on the timeline — the privacy reason for holding it back
            # (metadata-first until somebody approves) does not apply to a message somebody
            # went and fetched. A failure here is not the import failing: the row is its own
            # outbox and `google_gmail_sweep_bodies` re-tries it within five minutes.
            body_fetched = False
            try:
                body_fetched = await _fetch_body_with(
                    client,
                    SystemContext(org=ctx.org, session=ctx.session),
                    row.id,
                    message_id,
                    connection.user_id,
                )
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Manual gmail import: body fetch failed for %s (org %s)",
                    row.id,
                    ctx.org.id,
                    exc_info=True,
                )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_google_error(ctx, connection, exc)
        raise

    return GmailImportResult(
        interaction_id=row.id, subject=row.subject, body_fetched=body_fetched
    )
