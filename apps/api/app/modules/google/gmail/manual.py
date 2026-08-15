"""Pulling an email out of Gmail by hand, when the poller decided not to log it (#342, #372).

The ingest declines a message for one of ten reasons, and the two that hurt most are ordinary:
a message from somebody who is not a contact yet (`has_external_match`), and anything older
than the day the mailbox was connected — the first poll baselines and imports nothing, on
purpose. Add an expired `historyId` (Gmail keeps about a week), a deferral to a colleague's
mailbox that later opted out, an excluded label, an earlier rejection, and "why is this email
not on the timeline?" used to have no answer anybody could act on.

The thing worth noticing is that **almost none of those are blindness — they are decisions.**
The message is sitting in a mailbox we already hold a grant for. So this module does two
things: it lets the person who owns that mailbox find a message and override the decision, and
it tells them **which** decision it was.

Five rules hold it up.

**The id space is Google's, so the guard is Google's.** Every call here goes through
`acting_as(session, org, connection)` — the *caller's own* OAuth grant — and Gmail message ids
are meaningful only inside one mailbox. `messages.get` with your token cannot return a message
from a colleague's mailbox; a guessed, copied or brute-forced id answers 404. That is what makes
"accept an id from the client" safe here and unsafe almost everywhere else: it is not an id into
*our* tables, where the check would be ours to get right.

**Explaining is a dry run, not a log** (:mod:`~app.modules.google.gmail.gates`). Every reason
this module reports comes from `classify` — the *same* function the poller acts on — asked
about the one message somebody is looking at. Nothing is recorded for the thousands nobody will
ever ask about, and nothing about the gates is restated here, because an explainer that drifts
from the ingest answers confidently and wrongly. Two answers come from outside the gates on
purpose (`before_connection`, `never_offered`): for those messages the chain never ran, and
running it to produce a verdict would be the confident-and-wrong failure in its purest form.

**A reference resolves to a conversation.** The commonest complaint is not "this email" but
"the *rest* of this conversation" — a reply that never arrived, or the first message of a thread
whose later ones are all here. So one message named widens to its thread (#372): before that,
the better your reference the *less* you were shown, and the question people actually arrive
with — which of these are missing? — could only be asked by accident.

**Searching your own mailbox is allowed; browsing it is not.** This module used to refuse a
search outright, reasoning that "a picker means `messages.list` over arbitrary personal mail
inside the CRM … and it would make 'schakl only ever sees matched mail' untrue". The concern is
real and the conclusion was too strong, because that promise is about **the poller** — what the
integration ingests on its own — and was never a promise that the owner of a mailbox may not
look in it. Requiring them to go and copy an id out of Gmail by hand did not protect anything;
it just made the feature unusable. What answers the concern is the shape, and it is in
:func:`search`: the caller's own grant, named fields rather than raw Gmail operators, a hard
result ceiling, and nothing stored — content still arrives only on import.

**Gmail's web ids are not Gmail's API ids, and pretending otherwise fails silently.** What a
person copies out of the address bar today is usually an opaque `FMfcgz…` thread id from the
web UI, which the API does not accept and cannot convert. Three references *do* resolve, and
this module accepts exactly those: a hex id (what the API uses, and what older links and
`&th=` carry), a `msg-f:`/`thread-f:` decimal (the same id in the other base), and the RFC-822
`Message-ID` from "Toon origineel", looked up with `q=rfc822msgid:`. Anything else is refused
with a message naming what does work — and, since #372, search is the answer for somebody who
has none of the three.

**A refusal to guess.** Nothing here matches contacts, ranks companies or infers a client: the
caller says where the message is filed, exactly as an uploaded ``.eml`` does. Every one of the
matching rules that could have run is a rule that already declined this message once.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime, timedelta
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
from app.modules.google.gmail.gates import GateCache
from app.modules.google.gmail.models import GmailSuppression
from app.modules.google.gmail.service import (
    GMAIL_API,
    _excluded_label_id,
    _fetch_body_with,
    _internals,
    classify,
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
#: How many results one search may return. Low on purpose: this is "find the message I am
#: thinking of", and a list you have to page through is the mailbox browser this is not. It is
#: also a per-message Gmail call each, so the ceiling is a latency budget as much as a posture.
MAX_SEARCH_RESULTS = 20


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
    #: **Why this message is not on the timeline** — a :class:`~…gmail.gates.SkipReason` value,
    #: or ``null`` when the poller would have logged it. The dry run (#372): the same
    #: :func:`~app.modules.google.gmail.service.classify` the ingest runs, asked about this one
    #: message instead of performed on it. ``null`` beside ``logged=false`` is the interesting
    #: case and ``never_offered`` explains it.
    skip_reason: str | None = None
    #: The one or two strings the reason needs to be actionable: the excluded label's name, the
    #: colleague whose mailbox was deferred to. Never message content.
    skip_detail: dict[str, str] = Field(default_factory=dict)
    #: Sent before this mailbox was connected, so it was never offered to the poller at all —
    #: the first poll baselines and imports nothing, on purpose. Reported instead of a gate
    #: verdict, because running the gates on a message the chain never saw answers a question
    #: nobody asked and answers it confidently.
    before_connection: bool = False
    #: Every gate passes, it is newer than the connection, and it is still not here. The poller
    #: never saw it — a ``historyId`` gap (Gmail keeps about a week; a resync re-baselines) is
    #: the ordinary cause. Phrased as what we observed rather than as the cause, because per
    #: message the cause is genuinely unknowable.
    never_offered: bool = False


class GmailLookupResult(BaseModel):
    #: The thread these messages belong to, when the reference resolved to one.
    thread_id: str | None = None
    messages: list[GmailCandidate] = Field(default_factory=list)
    #: Gmail had more messages in this thread than we describe (``MAX_THREAD_MESSAGES``).
    truncated: bool = False
    #: The reference named one message and we widened it to its conversation (#372). The screen
    #: says so, because "I pasted one link and got eight messages" is otherwise surprising.
    widened_to_thread: bool = False


class GmailSearchResult(GmailLookupResult):
    """A search over the caller's own mailbox — the same rows, plus what was asked."""

    #: The Gmail query actually run, echoed back. The box takes fields, not raw Gmail syntax,
    #: so this is what the fields *became* — the one thing that makes an empty result
    #: diagnosable rather than mysterious.
    query: str = ""


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


async def _gate_cache(
    ctx: RequestContext,
    connection: GoogleConnection,
    candidates: list[GmailCandidate],
    rfc822_by_message: dict[str, str],
) -> GateCache:
    """The batch form of the four per-row lookups the gates make (``gates.GateCache``).

    Three queries for a whole conversation instead of four per message. The poller is right to
    ask one at a time — it *has* one at a time, in a worker — but fifty messages inside a
    request somebody is waiting on is the shape docs/PERFORMANCE.md calls invisible: correct at
    three rows, a stall at three hundred. Only the data source moves; every question is still
    asked by ``classify``.
    """
    sys_ctx = SystemContext(org=ctx.org, session=ctx.session)
    message_ids = [c.message_id for c in candidates if c.message_id]
    logged = await interactions_system.logged_state_for_messages(
        sys_ctx, connection.user_id, message_ids, list(rfc822_by_message.values())
    )
    suppressed_rows = (
        await ctx.session.execute(
            select(
                GmailSuppression.gmail_message_id, GmailSuppression.gmail_thread_id
            ).where(
                GmailSuppression.org_id == ctx.org.id,
                GmailSuppression.connection_id == connection.id,
            )
        )
    ).all()
    # ``logged`` is keyed by *either* kind of id (that is what makes the cross-mailbox dedup
    # answerable in one query), so split it back into the two the gates ask separately about.
    asked = set(message_ids)
    return GateCache(
        logged_message_ids=frozenset(k for k in logged if k in asked),
        logged_rfc822_ids=frozenset(k for k in logged if k not in asked),
        suppressed_message_ids=frozenset(row[0] for row in suppressed_rows if row[0]),
        suppressed_thread_ids=frozenset(row[1] for row in suppressed_rows if row[1]),
    )


async def _explain(
    ctx: RequestContext,
    connection: GoogleConnection,
    client,
    messages: list[dict],
    candidates: list[GmailCandidate],
    rfc822_by_message: dict[str, str],
) -> None:
    """Say, per message, why it is not on the timeline (#372) — the dry run.

    **The same decision the poller makes, asked rather than performed.** Every reason here
    comes from :func:`~app.modules.google.gmail.service.classify`; nothing about the gates is
    restated. That is the whole design constraint: an explainer that drifts from the ingest
    answers confidently and wrongly, which is worse than not answering — #324's *"named once
    and read twice"* applied to the chain instead of to one predicate.

    Two answers do **not** come from the gates, because for those messages the chain never ran:
    a message older than the connection was never offered (the first poll baselines and imports
    nothing, on purpose), and a message that passes every gate and is still not here was never
    seen either — the ordinary cause being a ``historyId`` gap, which is unknowable per message
    and so is reported as the observation rather than as the cause. Running the gates on those
    and printing a verdict would be the confident-and-wrong failure in its purest form.

    One Gmail call for the label id, shared across the batch; the rest is the poller's own
    per-message work against rows we already hold.
    """
    if not candidates:
        return
    settings_row = await google_settings_row(ctx.session, ctx.org.id)
    if settings_row is None:
        return
    # The owner's opt-out label, name → id: one call per batch, exactly as a poll does it.
    excluded_label_id = await _excluded_label_id(client, connection)
    internals = await _internals(ctx.session, ctx.org.id)
    cache = await _gate_cache(ctx, connection, candidates, rfc822_by_message)
    by_id = {str(m.get("id") or ""): m for m in messages}
    connected_at = connection.created_at
    for candidate in candidates:
        message = by_id.get(candidate.message_id)
        if message is None:
            continue
        if candidate.logged:
            # Our own record, not a decision about the message. Saying "already logged" twice
            # — once as a tick, once as a reason chip — is noise.
            continue
        decision = await classify(
            ctx.session,
            ctx.org,
            connection,
            settings_row,
            message,
            excluded_label_id,
            internals,
            cache,
        )
        candidate.skip_reason = decision.reason.value if decision.reason else None
        candidate.skip_detail = {k: v for k, v in decision.detail.items() if v}
        if candidate.occurred_at is not None and connected_at is not None:
            candidate.before_connection = candidate.occurred_at < connected_at
        # Passes every gate, is not older than the connection, and is still not here. The only
        # remaining explanation is that the poller never saw it.
        candidate.never_offered = decision.logs and not candidate.before_connection


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


async def _describe(
    ctx: RequestContext,
    connection: GoogleConnection,
    client,
    messages: list[dict],
    *,
    thread_id: str | None,
    widened: bool = False,
) -> GmailLookupResult:
    """The shared tail of every way in: build the rows, mark them, explain them.

    Three readers (a pasted reference, a conversation, a search) that used to end in three
    copies of the same four lines. The explainer is *in here* rather than at each call site for
    the ordinary reason — a surface that forgets to ask for it is a screen that silently offers
    less than the one beside it (#266's lesson about one of two callers remembering).
    """
    if not messages:
        raise AppError("not_found", "errors.gmail_message_not_found", status_code=404)
    internals = await _internals(ctx.session, ctx.org.id)
    truncated = len(messages) > MAX_THREAD_MESSAGES
    page = messages[:MAX_THREAD_MESSAGES]
    built, rfc822_ids = _build(page, internals.ours)
    await _decorate(ctx, connection, built, rfc822_ids)
    await _explain(ctx, connection, client, page, built, rfc822_ids)
    return GmailLookupResult(
        thread_id=thread_id or next((c.thread_id for c in built if c.thread_id), None),
        messages=built,
        truncated=truncated,
        widened_to_thread=widened,
    )


async def lookup(ctx: RequestContext, reference: str) -> GmailLookupResult:
    """Resolve a pasted reference to the conversation it names.

    **A reference resolves to a thread, not to a message** (#372). It used to answer with the
    single message when the id happened to be a message id, and with the whole conversation
    only when it did not — so the better your reference, the less you were shown, and the one
    question people actually arrive with ("which of these are missing?") could only be asked by
    accident. A message never travels alone anyway: an email you want to file is one turn of a
    conversation, and the turn before it is the context that says where it belongs.

    No extra reach: a thread is fetched by the id of a message we were just handed, through the
    caller's own grant, exactly as the fallback already did.
    """
    await _guard(
        ctx,
        bucket="gmail_manual_lookup",
        limit=settings.gmail_manual_lookup_rate_limit_per_minute,
    )
    parsed = parse_reference(reference)
    connection = await _my_connection(ctx)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            widened = False
            thread_id: str | None = None
            if parsed.kind == "rfc822":
                ids = await _by_rfc822(client, parsed.value)
                messages = [m for m in [await _get_metadata(client, i) for i in ids] if m]
            else:
                one = await _get_metadata(client, parsed.value)
                messages = [one] if one is not None else []
            if len(messages) == 1 and (messages[0].get("threadId") or "") not in ("", None):
                # One message named: show its conversation. The id we ask with came off the
                # message we were just handed, so this is the same mailbox and the same grant.
                thread_id = messages[0]["threadId"]
                thread = await _thread_payload(client, thread_id)
                siblings = (thread or {}).get("messages") or []
                if len(siblings) > 1:
                    messages, widened = siblings, True
            elif not messages:
                # Not a message id — the same id is very often the *thread's*, which is what a
                # link off a conversation carries. One extra call, and it turns the most common
                # paste into the gap-fill list rather than a "not found".
                thread = await _thread_payload(client, parsed.value)
                messages = (thread or {}).get("messages") or []
                thread_id = parsed.value if messages else None
            return await _describe(
                ctx, connection, client, messages, thread_id=thread_id, widened=widened
            )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — reported as a state, never as a 500
        await _handle_google_error(ctx, connection, exc)
        raise  # unreachable; _handle_google_error always raises


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
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            thread = await _thread_payload(client, thread_id)
            return await _describe(
                ctx,
                connection,
                client,
                (thread or {}).get("messages") or [],
                thread_id=thread_id,
            )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_google_error(ctx, connection, exc)
        raise


class GmailSearchQuery(BaseModel):
    """What the search box asks for. **Fields, never raw Gmail syntax.**

    A free-text box taking Gmail operators would put ``has:attachment larger:10M`` — and every
    other operator Google adds — inside the CRM, with no way to say afterwards what the search
    had been. Named fields mean the query we run is one we constructed, which is what makes the
    audit line ("searched their own mailbox for X") a true sentence rather than a copy of user
    input.
    """

    #: An address on the message, either end. One field rather than from/to, because "the mail
    #: with Devrim" is the question people have; which header it was on is not.
    participant: str | None = Field(default=None, max_length=320)
    #: Words in the subject.
    subject: str | None = Field(default=None, max_length=200)
    #: Inclusive date window, in the mailbox's own reading of a day.
    after: date | None = None
    before: date | None = None


def build_search_query(query: GmailSearchQuery) -> str:
    """The fields, as the one Gmail query string they become.

    Every value is quoted and stripped of the characters Gmail reads as syntax, so a colon or a
    space in an address cannot turn one field into three operators. This is the injection
    boundary and it is deliberately narrow: what comes out is only ever the operators named
    here, whatever went in.
    """
    parts: list[str] = []
    if query.participant:
        cleaned = _search_token(query.participant)
        if cleaned:
            # Either end of the message. Gmail's own grouping, so it stays one operator.
            parts.append(f"(from:{cleaned} OR to:{cleaned} OR cc:{cleaned})")
    if query.subject:
        cleaned = _search_token(query.subject)
        if cleaned:
            parts.append(f'subject:"{cleaned}"')
    if query.after:
        parts.append(f"after:{query.after.isoformat()}")
    if query.before:
        # Gmail's ``before:`` is exclusive; the field on the screen reads as inclusive.
        parts.append(f"before:{(query.before + timedelta(days=1)).isoformat()}")
    # Never the bin, never spam: this searches for a message somebody means to file.
    parts.append("-in:spam -in:trash")
    return " ".join(parts)


def _search_token(raw: str) -> str:
    """One field's value, with everything Gmail would read as syntax removed."""
    return re.sub(r'[":()\s]+', " ", raw).strip()[:200]


async def search(ctx: RequestContext, query: GmailSearchQuery) -> GmailSearchResult:
    """Find a message in the caller's **own** mailbox by who it was with, and when (#372).

    This module used to refuse a search outright, and the reasoning is worth stating because it
    was mostly right: *"a picker means messages.list over arbitrary personal mail inside the
    CRM, which is the trust landmine docs/GOOGLE.md names, and it would make 'schakl only ever
    sees matched mail' untrue."* Both halves still hold — and neither of them was ever an
    argument against **this**, because what the sentence is about is the *poller*. "schakl only
    ever sees matched mail" is a promise about what the integration ingests on its own; it was
    never a promise that the person whose mailbox it is cannot look in it. They can: it is
    their mailbox, it is open in the next tab, and every alternative on offer made them go and
    read an id out of it by hand and paste it back.

    What keeps the original concern answered is the shape, not the absence:

    * **The caller's own grant, always** (``acting_as`` with *their* connection). Gmail's own
      authorization is the boundary — the same thing that makes accepting a message id safe
      here and unsafe almost everywhere else.
    * **Fields, not free text** (:class:`GmailSearchQuery`). We construct the query, so we can
      say what was searched for; a raw operator box is user input we would be forwarding.
    * **Nothing is stored.** The results are metadata in one response — no row, no cache, no
      body. Content still arrives only when the caller imports a message, under the same grant.
    * **A hard ceiling** (:data:`MAX_SEARCH_RESULTS`), because a picker over an unbounded
      result set is the mailbox browser this is careful not to be.

    An empty query is refused rather than answered: with no fields it is "list my mailbox",
    which really is the thing nobody asked for.
    """
    await _guard(
        ctx,
        bucket="gmail_manual_search",
        limit=settings.gmail_manual_lookup_rate_limit_per_minute,
    )
    wire = build_search_query(query)
    if wire.strip() == "-in:spam -in:trash":
        raise AppError(
            "validation", "errors.gmail_search_empty", status_code=422,
            fields={"participant": "errors.gmail_search_empty"},
        )
    connection = await _my_connection(ctx)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            response = await client.get(
                f"{GMAIL_API}/messages",
                params={"q": wire, "maxResults": str(MAX_SEARCH_RESULTS)},
            )
            response.raise_for_status()
            ids = [m["id"] for m in (response.json().get("messages") or []) if m.get("id")]
            messages = [
                m for m in [await _get_metadata(client, i) for i in ids[:MAX_SEARCH_RESULTS]] if m
            ]
            if not messages:
                # An empty search is a state, not a 404: the fields were fine and the mailbox
                # has nothing matching them, and the screen says so beside the query it ran.
                return GmailSearchResult(query=wire)
            described = await _describe(ctx, connection, client, messages, thread_id=None)
            return GmailSearchResult(**described.model_dump(), query=wire)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_google_error(ctx, connection, exc)
        raise


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
