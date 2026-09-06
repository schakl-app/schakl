"""Pulling an email out of Outlook by hand, when the poller decided not to log it (#342, #372).

The rules are the Gmail importer's (``google/gmail/manual.py``) and are argued there: the id
space is the provider's, so the guard is the provider's (every read goes through the caller's
*own* grant, and a Graph message id means something only inside one mailbox); explaining is a
dry run through the same ``classify`` the poller acts on; a reference resolves to its whole
conversation; searching your own mailbox is allowed while browsing it is not (named fields, a
hard ceiling, nothing stored); and nothing here matches contacts or guesses a client.

Two things are Outlook's own. **What a person can paste** is a Graph id, an Outlook on the web
URL (whose ``/id/<id>`` segment Graph resolves for a message opened there), or the RFC-822
``Message-ID`` — looked up with a ``$filter`` on ``internetMessageId``, the one reference that is
always obtainable (Outlook → *View message source*). And **Graph refuses ``$orderby`` beside a
``$filter`` on another property** and beside ``$search`` altogether, so a conversation is fetched
unordered and sorted here.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import settings
from app.core.auth.ratelimit import limit_by_principal
from app.core.events import SystemContext
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.integrations.microsoft.client import acting_as, is_oauth_error, mark_connection_error
from app.integrations.microsoft.models import ConnectionStatus, MicrosoftConnection
from app.integrations.microsoft.oauth import has_mail_scope, microsoft_settings_row
from app.integrations.microsoft.outlook import matching
from app.integrations.microsoft.outlook.gates import GateCache
from app.integrations.microsoft.outlook.models import OutlookSuppression
from app.integrations.microsoft.outlook.service import (
    MESSAGE_SELECT,
    SOURCE,
    _fetch_body_with,
    _internals,
    classify,
    deep_link,
    well_known_folders,
)
from app.modules.interactions import system as interactions_system

logger = logging.getLogger("schakl.microsoft.outlook")

#: A Graph message id: base64-ish, and long enough not to match a stray word someone pasted.
_GRAPH_ID = re.compile(r"^[A-Za-z0-9_=+/-]{20,512}$")
#: An RFC 5322 ``Message-ID``, with or without its angle brackets.
_RFC822_ID = re.compile(r"^<?([^\s<>@]+@[^\s<>@]+)>?$")
#: Outlook on the web hosts whose ``/id/<id>`` path segment names a message.
_OWA_HOSTS = ("outlook.office.com", "outlook.office365.com", "outlook.live.com")
MAX_THREAD_MESSAGES = 50
MAX_SEARCH_RESULTS = 20


class OutlookCandidate(BaseModel):
    """One message the caller could log — the Gmail candidate's exact shape, so the web draws
    both feeds' pickers with one component."""

    message_id: str
    thread_id: str | None = None
    subject: str | None = None
    from_email: str | None = None
    from_name: str | None = None
    recipients: str | None = None
    occurred_at: datetime | None = None
    snippet: str | None = None
    direction: str = "none"
    logged: bool = False
    interaction_id: uuid.UUID | None = None
    suppressed: bool = False
    skip_reason: str | None = None
    skip_detail: dict[str, str] = Field(default_factory=dict)
    before_connection: bool = False
    never_offered: bool = False


class OutlookLookupResult(BaseModel):
    thread_id: str | None = None
    messages: list[OutlookCandidate] = Field(default_factory=list)
    truncated: bool = False
    widened_to_thread: bool = False


class OutlookSearchResult(OutlookLookupResult):
    #: The KQL actually run, echoed back — what the fields *became*.
    query: str = ""


class OutlookImportResult(BaseModel):
    interaction_id: uuid.UUID
    subject: str | None = None
    body_fetched: bool = False


class Reference(BaseModel):
    kind: str  # "id" | "rfc822"
    value: str


def parse_reference(raw: str) -> Reference:
    """Turn whatever was pasted into something Graph will answer. Raises rather than guessing."""
    text = (raw or "").strip()
    if not text:
        raise AppError(
            "validation",
            "errors.outlook_reference_unreadable",
            status_code=422,
            fields={"reference": "errors.required"},
        )
    candidate = text
    parsed = urlparse(text) if "://" in text else None
    if parsed is not None and (parsed.hostname or "").lower() in _OWA_HOSTS:
        segments = [unquote(s) for s in parsed.path.split("/") if s]
        candidate = ""
        for index, segment in enumerate(segments):
            if segment == "id" and index + 1 < len(segments):
                candidate = segments[index + 1]
                break
        if not candidate:
            raise AppError(
                "validation",
                "errors.outlook_reference_unreadable",
                status_code=422,
                fields={"reference": "errors.outlook_reference_unreadable"},
            )
    rfc822 = _RFC822_ID.match(candidate)
    if rfc822:
        return Reference(kind="rfc822", value=f"<{rfc822.group(1)}>")
    if _GRAPH_ID.match(candidate):
        return Reference(kind="id", value=candidate)
    raise AppError(
        "validation",
        "errors.outlook_reference_unreadable",
        status_code=422,
        fields={"reference": "errors.outlook_reference_unreadable"},
    )


# --------------------------------------------------------------------------- #
# The caller's own mailbox
# --------------------------------------------------------------------------- #
async def _my_connection(ctx: RequestContext) -> MicrosoftConnection:
    row = await microsoft_settings_row(ctx.session, ctx.org.id)
    if row is None or not row.outlook_enabled:
        raise AppError("outlook_disabled", "errors.outlook_disabled", status_code=409)
    connection = await ctx.session.scalar(
        select(MicrosoftConnection).where(
            MicrosoftConnection.org_id == ctx.org.id,
            MicrosoftConnection.user_id == ctx.user.id,
        )
    )
    if connection is None:
        raise AppError("microsoft_not_connected", "errors.microsoft_not_connected", status_code=409)
    if not has_mail_scope(connection.scopes):
        # Not ``outlook_sync_enabled``: opting the feed in and reaching for one named message
        # are different consents, and someone who keeps the feed off may still file one email.
        raise AppError("outlook_sync_off", "errors.outlook_sync_off", status_code=409)
    if connection.status != ConnectionStatus.ACTIVE.value:
        raise AppError(
            "microsoft_connection_error", "errors.microsoft_connection_error", status_code=409
        )
    return connection


async def _guard(ctx: RequestContext, *, bucket: str, limit: int) -> None:
    """Both keys, then the ceiling (#310): reading your own mailbox through our API is
    ``microsoft.connection.manage``, turning it into a contactmoment is
    ``interactions.interaction.write``."""
    ctx.require("microsoft.connection.manage")
    ctx.require("interactions.interaction.write")
    await limit_by_principal(bucket=bucket, principal=f"{ctx.org.id}:{ctx.user.id}", limit=limit)


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def _escape_odata(value: str) -> str:
    return value.replace("'", "''")


def _candidate(
    message: dict, *, ours: frozenset[str], folders: matching.WellKnownFolders
) -> OutlookCandidate:
    participants = matching.participants_of(message)
    sender = matching.sender_of(participants)
    from_participant = next((p for p in participants if p.get("role") == "from"), None)
    others = [p for p in participants if p.get("role") != "from"]
    received = matching.parse_graph_datetime(
        message.get("receivedDateTime") or message.get("sentDateTime") or ""
    )
    return OutlookCandidate(
        message_id=str(message.get("id") or ""),
        thread_id=message.get("conversationId"),
        subject=message.get("subject") or None,
        from_email=sender,
        from_name=(from_participant or {}).get("name") or None,
        recipients=", ".join(p["email"] for p in others[:5]) or None,
        occurred_at=received,
        snippet=matching.clean_snippet(message.get("bodyPreview")),
        direction=matching.direction_of(message, folders, sender_internal=sender in ours),
    )


async def _decorate(
    ctx: RequestContext,
    connection: MicrosoftConnection,
    candidates: list[OutlookCandidate],
    rfc822_by_message: dict[str, str],
) -> None:
    """Mark what is already logged and what this mailbox rejected earlier — two queries, flat."""
    if not candidates:
        return
    sys_ctx = SystemContext(org=ctx.org, session=ctx.session)
    message_ids = [c.message_id for c in candidates if c.message_id]
    logged = await interactions_system.logged_state_for_messages(
        sys_ctx, connection.user_id, message_ids, list(rfc822_by_message.values())
    )
    thread_ids = {c.thread_id for c in candidates if c.thread_id}
    suppressed_rows = (
        await ctx.session.execute(
            select(OutlookSuppression.message_id, OutlookSuppression.conversation_id).where(
                OutlookSuppression.org_id == ctx.org.id,
                OutlookSuppression.connection_id == connection.id,
            )
        )
    ).all()
    suppressed_messages = {row[0] for row in suppressed_rows if row[0]}
    suppressed_threads = {row[1] for row in suppressed_rows if row[1]} & thread_ids
    for candidate in candidates:
        row_id = logged.get(candidate.message_id) or logged.get(
            rfc822_by_message.get(candidate.message_id, "")
        )
        candidate.logged = row_id is not None
        candidate.interaction_id = row_id
        candidate.suppressed = (
            candidate.message_id in suppressed_messages or candidate.thread_id in suppressed_threads
        )


async def _gate_cache(
    ctx: RequestContext,
    connection: MicrosoftConnection,
    candidates: list[OutlookCandidate],
    rfc822_by_message: dict[str, str],
) -> GateCache:
    """The batch form of the four per-row lookups the gates make — three queries for a whole
    conversation instead of four per message (docs/PERFORMANCE.md)."""
    sys_ctx = SystemContext(org=ctx.org, session=ctx.session)
    message_ids = [c.message_id for c in candidates if c.message_id]
    logged = await interactions_system.logged_state_for_messages(
        sys_ctx, connection.user_id, message_ids, list(rfc822_by_message.values())
    )
    suppressed_rows = (
        await ctx.session.execute(
            select(OutlookSuppression.message_id, OutlookSuppression.conversation_id).where(
                OutlookSuppression.org_id == ctx.org.id,
                OutlookSuppression.connection_id == connection.id,
            )
        )
    ).all()
    asked = set(message_ids)
    return GateCache(
        logged_message_ids=frozenset(k for k in logged if k in asked),
        logged_rfc822_ids=frozenset(k for k in logged if k not in asked),
        suppressed_message_ids=frozenset(row[0] for row in suppressed_rows if row[0]),
        suppressed_thread_ids=frozenset(row[1] for row in suppressed_rows if row[1]),
    )


async def _explain(
    ctx: RequestContext,
    connection: MicrosoftConnection,
    messages: list[dict],
    candidates: list[OutlookCandidate],
    rfc822_by_message: dict[str, str],
    folders: matching.WellKnownFolders,
) -> None:
    """Say, per message, why it is not on the timeline — the same decision the poller makes,
    asked rather than performed. Two answers come from outside the gates (``before_connection``,
    ``never_offered``), because for those messages the chain never ran."""
    if not candidates:
        return
    settings_row = await microsoft_settings_row(ctx.session, ctx.org.id)
    if settings_row is None:
        return
    internals = await _internals(ctx.session, ctx.org.id)
    cache = await _gate_cache(ctx, connection, candidates, rfc822_by_message)
    by_id = {str(m.get("id") or ""): m for m in messages}
    connected_at = connection.created_at
    for candidate in candidates:
        message = by_id.get(candidate.message_id)
        if message is None or candidate.logged:
            continue
        decision = await classify(
            ctx.session, ctx.org, connection, settings_row, message, folders, internals, cache
        )
        candidate.skip_reason = decision.reason.value if decision.reason else None
        candidate.skip_detail = {k: v for k, v in decision.detail.items() if v}
        if candidate.occurred_at is not None and connected_at is not None:
            candidate.before_connection = candidate.occurred_at < connected_at
        candidate.never_offered = decision.logs and not candidate.before_connection


async def _handle_graph_error(
    ctx: RequestContext, connection: MicrosoftConnection, exc: Exception
) -> None:
    if await is_oauth_error(exc):
        await mark_connection_error(ctx.session, ctx.org, connection, str(exc))
        raise AppError(
            "microsoft_connection_error", "errors.microsoft_connection_error", status_code=409
        ) from exc
    logger.exception("Outlook manual read failed for connection %s", connection.id)
    raise AppError("outlook_unavailable", "errors.outlook_unavailable", status_code=502) from exc


async def _get_message(client, message_id: str) -> dict | None:
    response = await client.get(f"/me/messages/{message_id}", params={"$select": MESSAGE_SELECT})
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()


async def _filtered(client, odata_filter: str, top: int) -> list[dict]:
    """A ``$filter`` listing, sorted here by receipt because Graph will not sort it for us."""
    response = await client.get(
        "/me/messages",
        params={"$filter": odata_filter, "$top": str(top), "$select": MESSAGE_SELECT},
    )
    response.raise_for_status()
    messages = [m for m in (response.json() or {}).get("value") or [] if m.get("id")]
    return _sorted(messages)


def _sorted(messages: list[dict]) -> list[dict]:
    return sorted(
        messages,
        key=lambda m: (
            matching.parse_graph_datetime(m.get("receivedDateTime") or "")
            or datetime.min.replace(tzinfo=UTC)
        ),
    )


async def _by_rfc822(client, rfc822_id: str) -> list[dict]:
    """A lookup wearing a filter's clothes: ``internetMessageId`` names exactly one message."""
    return await _filtered(client, f"internetMessageId eq '{_escape_odata(rfc822_id)}'", 5)


async def _conversation(client, conversation_id: str) -> list[dict]:
    return await _filtered(
        client, f"conversationId eq '{_escape_odata(conversation_id)}'", MAX_THREAD_MESSAGES + 1
    )


def _build(
    messages: list[dict], ours: frozenset[str], folders: matching.WellKnownFolders
) -> tuple[list[OutlookCandidate], dict[str, str]]:
    built: list[OutlookCandidate] = []
    rfc822_by_message: dict[str, str] = {}
    for message in messages:
        candidate = _candidate(message, ours=ours, folders=folders)
        rfc822 = matching.rfc822_id_of(message)
        if rfc822 and candidate.message_id:
            rfc822_by_message[candidate.message_id] = rfc822
        built.append(candidate)
    built.sort(key=lambda c: c.occurred_at or datetime.min.replace(tzinfo=UTC))
    return built, rfc822_by_message


async def _describe(
    ctx: RequestContext,
    connection: MicrosoftConnection,
    client,
    messages: list[dict],
    *,
    thread_id: str | None,
    widened: bool = False,
) -> OutlookLookupResult:
    """The shared tail of every way in: build the rows, mark them, explain them."""
    if not messages:
        raise AppError("not_found", "errors.outlook_message_not_found", status_code=404)
    internals = await _internals(ctx.session, ctx.org.id)
    folders = await well_known_folders(client)
    truncated = len(messages) > MAX_THREAD_MESSAGES
    page = messages[:MAX_THREAD_MESSAGES]
    built, rfc822_ids = _build(page, internals.ours, folders)
    await _decorate(ctx, connection, built, rfc822_ids)
    await _explain(ctx, connection, page, built, rfc822_ids, folders)
    return OutlookLookupResult(
        thread_id=thread_id or next((c.thread_id for c in built if c.thread_id), None),
        messages=built,
        truncated=truncated,
        widened_to_thread=widened,
    )


async def lookup(ctx: RequestContext, reference: str) -> OutlookLookupResult:
    """Resolve a pasted reference to the conversation it names (#372: a reference resolves to a
    thread, not to a message — the question people arrive with is "which of these are missing?")."""
    await _guard(
        ctx,
        bucket="outlook_manual_lookup",
        limit=settings.outlook_manual_lookup_rate_limit_per_minute,
    )
    parsed = parse_reference(reference)
    connection = await _my_connection(ctx)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            widened = False
            thread_id: str | None = None
            if parsed.kind == "rfc822":
                messages = await _by_rfc822(client, parsed.value)
            else:
                one = await _get_message(client, parsed.value)
                messages = [one] if one is not None else []
            if len(messages) == 1 and messages[0].get("conversationId"):
                thread_id = messages[0]["conversationId"]
                siblings = await _conversation(client, thread_id)
                if len(siblings) > 1:
                    messages, widened = siblings, True
            return await _describe(
                ctx, connection, client, messages, thread_id=thread_id, widened=widened
            )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 — reported as a state, never as a 500
        await _handle_graph_error(ctx, connection, exc)
        raise  # unreachable; _handle_graph_error always raises


async def thread_messages(ctx: RequestContext, conversation_id: str) -> OutlookLookupResult:
    """Every message of one conversation, marked with what is already on the timeline. The id
    came off a row we logged, so this asks about a conversation we were already told about."""
    await _guard(
        ctx,
        bucket="outlook_manual_lookup",
        limit=settings.outlook_manual_lookup_rate_limit_per_minute,
    )
    connection = await _my_connection(ctx)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            messages = await _conversation(client, conversation_id)
            return await _describe(ctx, connection, client, messages, thread_id=conversation_id)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_graph_error(ctx, connection, exc)
        raise


class OutlookSearchQuery(BaseModel):
    """What the search box asks for. **Fields, never raw KQL.**"""

    participant: str | None = Field(default=None, max_length=320)
    subject: str | None = Field(default=None, max_length=200)
    after: date | None = None
    before: date | None = None


def build_search_query(query: OutlookSearchQuery) -> str:
    """The fields, as the one KQL string they become — the injection boundary, deliberately
    narrow: every value is stripped of what KQL reads as syntax and quoted as a phrase."""
    parts: list[str] = []
    if query.participant:
        cleaned = matching.search_token(query.participant)
        if cleaned:
            parts.append(f'participants:"{cleaned}"')
    if query.subject:
        cleaned = matching.search_token(query.subject)
        if cleaned:
            parts.append(f'subject:"{cleaned}"')
    if query.after:
        parts.append(f"received>={query.after.isoformat()}")
    if query.before:
        parts.append(f"received<={query.before.isoformat()}")
    return " AND ".join(parts)


async def search(ctx: RequestContext, query: OutlookSearchQuery) -> OutlookSearchResult:
    """Find a message in the caller's **own** mailbox by who it was with, and when (#372): the
    caller's own grant, named fields, a hard ceiling, nothing stored."""
    await _guard(
        ctx,
        bucket="outlook_manual_search",
        limit=settings.outlook_manual_lookup_rate_limit_per_minute,
    )
    kql = build_search_query(query)
    if not kql:
        raise AppError(
            "validation",
            "errors.outlook_search_empty",
            status_code=422,
            fields={"participant": "errors.outlook_search_empty"},
        )
    connection = await _my_connection(ctx)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            # Graph's ``$search`` wants the whole KQL expression inside one pair of quotes;
            # the phrase quotes inside are escaped so a subject cannot end the expression.
            wire = '"' + kql.replace('"', '\\"') + '"'
            response = await client.get(
                "/me/messages",
                params={
                    "$search": wire,
                    "$top": str(MAX_SEARCH_RESULTS),
                    "$select": MESSAGE_SELECT,
                },
            )
            response.raise_for_status()
            messages = [m for m in (response.json() or {}).get("value") or [] if m.get("id")]
            if not messages:
                return OutlookSearchResult(query=kql)
            described = await _describe(
                ctx, connection, client, _sorted(messages[:MAX_SEARCH_RESULTS]), thread_id=None
            )
            return OutlookSearchResult(**described.model_dump(), query=kql)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_graph_error(ctx, connection, exc)
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
) -> OutlookImportResult:
    """Log one named message, then fetch its body the way an approval does — and only then
    make the "let schakl fill the task in" offer, for the ordering reason the Gmail importer
    states: the job it queues must not race the commit that makes the row claimable."""
    await _guard(
        ctx,
        bucket="outlook_manual_import",
        limit=settings.outlook_manual_import_rate_limit_per_minute,
    )
    connection = await _my_connection(ctx)
    internals = await _internals(ctx.session, ctx.org.id)
    try:
        async with acting_as(ctx.session, ctx.org, connection) as client:
            message = await _get_message(client, message_id)
            if message is None:
                raise AppError("not_found", "errors.outlook_message_not_found", status_code=404)
            participants = matching.participants_of(message)
            if not participants:
                raise AppError("validation", "errors.outlook_message_unusable", status_code=422)
            folders = await well_known_folders(client)
            sender = matching.sender_of(participants)
            row = await interactions_system.record_manual_mailbox_email(
                ctx,
                source=SOURCE,
                owner_user_id=connection.user_id,
                owner_name=ctx.user.full_name or ctx.user.email,
                occurred_at=matching.occurred_at_of(message),
                subject=message.get("subject") or None,
                snippet=matching.clean_snippet(message.get("bodyPreview")),
                direction=matching.direction_of(
                    message, folders, sender_internal=sender in internals.ours
                ),
                participants=participants,
                gmail_message_id=message_id,
                gmail_thread_id=message.get("conversationId"),
                rfc822_message_id=matching.rfc822_id_of(message),
                deep_link=deep_link(message),
                links=links,
                allow_duplicate=allow_duplicate,
            )
            body_fetched = False
            try:
                body_fetched = await _fetch_body_with(
                    client,
                    SystemContext(org=ctx.org, session=ctx.session),
                    row.id,
                    message_id,
                    connection.user_id,
                )
            except Exception:  # noqa: BLE001 — the row is its own outbox; the sweep re-tries
                logger.warning(
                    "Manual Outlook import: body fetch failed for %s (org %s)",
                    row.id,
                    ctx.org.id,
                    exc_info=True,
                )
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001
        await _handle_graph_error(ctx, connection, exc)
        raise

    if enrich_task:
        await interactions_system.offer_task_enrichment(ctx, row)

    return OutlookImportResult(
        interaction_id=row.id, subject=row.subject, body_fetched=body_fetched
    )
