"""Intake addresses — an address the agency owns that turns a mail into a record.

``taak@bureau.nl``: an employee forwards a client's mail there with a line of instruction, and a
task appears. The rule is the agency's, not the wire's, so it lives beside ``matching`` and
``gates`` rather than in either mailbox feed — and, because §6 forbids the feeds importing the
module that owns the record, it is a **registry**: whoever owns the record registers the
addresses it answers on and a handler, and both feeds hand every message addressed to one of
them to :func:`dispatch` without knowing what comes out the other end.

Three things the seam states on purpose.

**The sender is a colleague or the mail is nothing.** Both feeds already process a colleague's
*sent* mail, so a message from Jan to ``taak@`` arrives through Jan's own connected mailbox —
the one copy whose authorship the provider vouches for. A copy that arrives some other way (a
shared box the alias delivers into) proves only a ``From`` header; the feed resolves that
header against :class:`~app.core.mailbox.internals.Internals` (``owner_by_email``, which
excludes client logins by construction), and an address that resolves to nobody is refused
before anything is stored. Nothing an outsider writes to the address becomes a row.

**A message is one act, whichever copy arrives first.** The same email sits in the sender's
mailbox and in every colleague's it was copied to, and two feeds may poll it in either order;
the *handler* is responsible for making a second copy a no-op (a unique index on the RFC-822
``Message-ID`` per org, docs/PAYMENTS.md's rule) and for answering with what the first copy
produced, so the feed can still link the interaction half of a mail to it.

**Intake never suppresses the timeline.** A colleague who copies ``taak@`` on a live client
thread wants both things: the task, and the contact moment filed onto it. So the feeds ask
:func:`intake_target` *before* the chatter and match gates, strip the intake address out of the
participant list, and let the rest of the chain decide the interaction half as if the address
had never been on the mail — carrying the outcome's ``links`` into the row's mappings.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.events import EmitContext

logger = logging.getLogger("schakl.mailbox.intake")

#: Header roles an intake address counts on. ``from`` is deliberately absent: a mail *from*
#: the intake address is a bounce or an auto-reply, never an instruction.
_RECIPIENT_ROLES = frozenset({"to", "cc"})


@dataclass(frozen=True)
class IntakeAddress:
    """One address an org has designated, and the registry key of whoever answers on it."""

    email: str
    key: str


@dataclass(frozen=True)
class IntakeAttachment:
    """One part of the message with bytes: an attachment, or an inline image the body points at
    (``content_id`` set, the e-mail ``cid:`` shape the storage core already renders)."""

    filename: str
    content_type: str
    data: bytes
    content_id: str | None = None


@dataclass
class IntakeMessage:
    """The message as a handler needs it — normalised by the feed, provider-agnostic here.

    ``sender_user_id`` is the colleague the ``From`` address resolved to, or ``None`` when it
    resolved to nobody (the handler refuses those). ``body_markdown`` is set only when the
    message had an HTML part (docs/GOOGLE.md §6: a received body is not our markdown).
    """

    address: IntakeAddress
    source: str
    sender_email: str
    sender_name: str | None
    sender_user_id: uuid.UUID | None
    subject: str | None
    body_text: str | None
    body_markdown: str | None
    participants: list[dict[str, Any]]
    attachments: list[IntakeAttachment]
    rfc822_message_id: str | None
    provider_message_id: str
    provider_thread_id: str | None
    occurred_at: datetime
    deep_link: str | None = None
    #: Who counts as us, for the handler's own address matching — resolved once per poll by the
    #: feed, so the handler does not load it again per message.
    internals: Any = None


@dataclass(frozen=True)
class IntakeOutcome:
    """What the handler did. ``links`` are mapping columns the feed may copy onto the
    interaction half of the same mail (``task_id``, ``company_id`` — the interactions module's
    ``MAPPING_FIELDS`` vocabulary), so a contact moment lands filed on the task the mail made."""

    #: ``created`` | ``parked`` | ``duplicate`` | ``refused``
    status: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None
    reason: str | None = None
    links: dict[str, Any] = field(default_factory=dict)

    @property
    def acted(self) -> bool:
        return self.status in {"created", "parked"}


#: ``(session, org_id)`` → the addresses this module answers on for the org. Called with the RLS
#: GUC bound; a provider reads its own settings table and nothing else.
IntakeAddressProvider = Callable[[AsyncSession, uuid.UUID], Awaitable[list[IntakeAddress]]]
IntakeHandler = Callable[[EmitContext, IntakeMessage], Awaitable[IntakeOutcome]]


@dataclass(frozen=True)
class _Registration:
    addresses: IntakeAddressProvider
    handler: IntakeHandler


_registrations: dict[str, _Registration] = {}


def register_intake(key: str, *, addresses: IntakeAddressProvider, handler: IntakeHandler) -> None:
    """Called once by the owning module at import time."""
    _registrations[key] = _Registration(addresses=addresses, handler=handler)


def registered_intakes() -> list[str]:
    return sorted(_registrations)


async def load_intake_addresses(session: AsyncSession, org_id: uuid.UUID) -> dict[str, str]:
    """``{address: key}`` for every registered intake, lower-cased. A provider that raises is
    logged and skipped — one module's fault must not blind the feed to the rest of the mail."""
    found: dict[str, str] = {}
    for key, registration in sorted(_registrations.items()):
        try:
            addresses = await registration.addresses(session, org_id)
        except Exception:  # noqa: BLE001 — see docstring
            logger.warning("intake address provider %s failed", key, exc_info=True)
            continue
        for address in addresses:
            email = (address.email or "").strip().lower()
            if email:
                found.setdefault(email, address.key)
    return found


def intake_target(
    participants: Iterable[dict[str, Any]], addresses: dict[str, str]
) -> IntakeAddress | None:
    """The first intake address among the message's **recipients**, or ``None``."""
    if not addresses:
        return None
    for participant in participants:
        if participant.get("role") not in _RECIPIENT_ROLES:
            continue
        email = (participant.get("email") or "").lower()
        key = addresses.get(email)
        if key is not None:
            return IntakeAddress(email=email, key=key)
    return None


def without_intake(
    participants: list[dict[str, Any]], addresses: dict[str, str]
) -> list[dict[str, Any]]:
    """The participant list with every intake address taken out — what the rest of the ingest
    chain reads, so a mail to ``taak@`` and one colleague is colleague-only chatter as before,
    and a client thread with ``taak@`` in Cc is still a client thread."""
    return [p for p in participants if (p.get("email") or "").lower() not in addresses]


def merge_links(mappings: dict[str, Any], outcome: IntakeOutcome | None) -> dict[str, Any]:
    """The interaction half's mappings, with the intake's task folded in: the contact moment a
    colleague copied ``taak@`` on lands filed onto the task that same mail made, and inherits
    the task's client when the thread matched none of its own."""
    if outcome is None or not outcome.links:
        return mappings
    merged = dict(mappings)
    task_id = outcome.links.get("task_id")
    if task_id is not None:
        roster = [task_id, *(merged.get("task_ids") or [])]
        if merged.get("task_id") and merged["task_id"] not in roster:
            roster.append(merged["task_id"])
        merged["task_id"] = task_id
        merged["task_ids"] = list(dict.fromkeys(roster))
    if merged.get("company_id") is None and outcome.links.get("company_id") is not None:
        merged["company_id"] = outcome.links["company_id"]
    return merged


async def dispatch(ctx: EmitContext, message: IntakeMessage) -> IntakeOutcome:
    """Hand one message to the module that owns its address."""
    registration = _registrations.get(message.address.key)
    if registration is None:
        # An address whose module has since been disabled: nothing answers, and the feed's
        # ordinary gates decide the message as if the address were any other stranger's.
        return IntakeOutcome(status="refused", reason="no_handler")
    return await registration.handler(ctx, message)


__all__ = [
    "IntakeAddress",
    "IntakeAttachment",
    "IntakeHandler",
    "IntakeMessage",
    "IntakeOutcome",
    "dispatch",
    "intake_target",
    "load_intake_addresses",
    "merge_links",
    "register_intake",
    "registered_intakes",
    "without_intake",
]
