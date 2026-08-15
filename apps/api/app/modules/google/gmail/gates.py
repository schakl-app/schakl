"""Why one email was, or was not, logged — the decision named once so it can be read twice.

The poller used to interleave the fetch, nine bare ``return 0``s and the write in a single
function. That made the decision **unaskable**: the only way to find out what it would do with
a message was to let it do it, and the only record afterwards was the absence of a row. "Why
did this email never appear?" cost an afternoon and a database, which the code comment on the
one skip that *did* log admitted in as many words.

So the chain is a function that decides (:func:`~app.modules.google.gmail.service.classify`)
and a caller that acts. The poller acts on the decision; the explainer behind the manual
importer asks for one and renders it. **Both call the same function** — this is #324's lesson
(*"the predicate is now named once and read twice, because two copies of it is how they came
to disagree"*) applied to the whole chain rather than to one predicate. An explainer that
drifts from the ingest is worse than no explainer, because it answers confidently and wrongly.

**It is a dry run, not a log.** The tempting shape is a `gmail_skips` row per decision, and it
is the wrong one twice over: it is a record of *every email you receive* — newsletters,
supplier invoices, password resets, GitHub notifications (#324's inventory of a real mailbox) —
which is strictly more than the module's promise that schakl only ever sees matched mail; and
it answers speculatively for thousands of messages nobody will ever ask about. The question is
asked about **one** message, by somebody looking at it, through the fetch they already asked
for. Zero storage, no volume, no privacy cost beyond the one they requested.

The two exceptions are in :class:`~app.modules.google.gmail.models.GmailSkip`, and the test for
being there is not "is it useful" but **"is it a failure rather than a policy, and would the
user never know to look?"** Two qualify (``DEFERRED_TO_OWNER``, ``INGEST_ERROR``); the other
nine are policy the dry run explains perfectly well on demand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class SkipReason(StrEnum):
    """Every way the ingest can decline a message, in the order the chain asks.

    The values are stable strings: they are i18n key suffixes on the web
    (``interactions.gmail.skip.<value>``) and, for two of them, stored in ``gmail_skips``. A
    rename is a migration plus a message catalog, so they are named for what is true of the
    message rather than for the function that noticed it.
    """

    #: Already a contactmoment from *this* mailbox. Not a skip anyone needs explaining — the
    #: screen says "gelogd" — but the chain has to answer it before it answers anything else.
    ALREADY_LOGGED = "already_logged"
    #: A colleague's mailbox logged this same email (the cross-mailbox RFC-822 dedup). One
    #: email is one timeline entry, whichever mailbox happened to poll first.
    LOGGED_ELSEWHERE = "logged_elsewhere"
    #: The owner rejected this message in review; it must never come back on its own.
    SUPPRESSED_MESSAGE = "suppressed_message"
    #: The owner rejected the whole conversation.
    SUPPRESSED_THREAD = "suppressed_thread"
    #: The owner's opt-out label is on it. ``detail["label"]`` names it, because "a label
    #: excluded it" is unactionable when you cannot see which.
    EXCLUDED_LABEL = "excluded_label"
    #: A draft, a chat, spam or trash — never a message that was sent or received.
    NOT_A_MESSAGE = "not_a_message"
    #: No From/To/Cc at all: nothing to file it under and nobody to attribute it to.
    NO_PARTICIPANTS = "no_participants"
    #: A copy of a colleague's mail whose own mailbox polls and will log it.
    #: ``detail["owner"]`` names them.
    DEFERRED_TO_OWNER = "deferred_to_owner"
    #: Colleague-to-colleague, and the org has internal logging switched off.
    INTERNAL_ONLY = "internal_only"
    #: Nobody on it is a contact we know from outside the agency (#324) — by a wide margin the
    #: commonest answer, and the only one whose fix is "add the contact".
    NO_EXTERNAL_MATCH = "no_external_match"
    #: Gmail no longer has it (deleted between the history entry and the fetch).
    GONE = "gone"
    #: The ingest raised on it. Not a decision at all — the one reason that is *ours*.
    INGEST_ERROR = "ingest_error"


#: The reasons worth a row in ``gmail_skips``. Both are failures rather than policy, both are
#: rare, and neither is something the user would ever know to go looking for: a deferral to a
#: mailbox that then stops polling loses the email outright, and a poison message is skipped
#: precisely so it cannot wedge the feed. Everything else the dry run answers on demand.
PERSISTED_REASONS: frozenset[SkipReason] = frozenset(
    {SkipReason.DEFERRED_TO_OWNER, SkipReason.INGEST_ERROR}
)

#: Reasons that describe *our own* record rather than a decision about the message. The screen
#: renders these as state, not as a fault to be explained away.
ALREADY_HERE: frozenset[SkipReason] = frozenset(
    {SkipReason.ALREADY_LOGGED, SkipReason.LOGGED_ELSEWHERE}
)


@dataclass(frozen=True)
class Decision:
    """What the ingest chain concluded about one message.

    ``reason is None`` means *log it*, and then ``mappings`` and ``pending`` say how. Anything
    else means the chain declined, and nothing after that gate was evaluated — which is worth
    stating, because it is why the explainer reports **the first** reason rather than a list:
    the gates are ordered, and "it also has no contact match" is not something we know about a
    message we stopped reading at the excluded label.
    """

    reason: SkipReason | None = None
    #: Where it would be filed (company/project/contact ids), when it would be logged.
    mappings: dict[str, Any] = field(default_factory=dict)
    #: Would it wait for review, or land logged? Meaningless unless ``reason is None``.
    pending: bool = True
    #: Small, non-identifying extras the message needs in order to be actionable — a label
    #: name, a colleague's display name. Never the subject, never the participants: this
    #: travels into a stored row for two of the reasons, and a skip log that carries content
    #: is the log of every email you receive that this design exists to refuse.
    detail: dict[str, str] = field(default_factory=dict)

    @property
    def logs(self) -> bool:
        return self.reason is None


@dataclass
class GateCache:
    """Batch answers for a caller classifying **many** messages in one request.

    The poller asks about one message at a time, in a worker, so six queries per message costs
    it nothing. The explainer asks about a whole conversation inside a request somebody is
    waiting on — fifty messages would be three hundred queries, which is the shape
    `docs/PERFORMANCE.md` calls invisible: perfect at three rows and a stall at three hundred.

    So the *questions* stay in one function (:func:`~…gmail.service.classify`) and only their
    **data source** is injected. Nothing here re-implements a gate; each field is the batched
    form of one lookup the gate would otherwise make per row, and a ``None`` field means "ask
    the database", which is what the poller does for all of them.
    """

    #: Gmail message ids already logged from this mailbox.
    logged_message_ids: frozenset[str] | None = None
    #: RFC-822 ``Message-ID``s already logged from *any* mailbox (the cross-mailbox dedup).
    logged_rfc822_ids: frozenset[str] | None = None
    #: Message-level and thread-level suppressions for this connection.
    suppressed_message_ids: frozenset[str] | None = None
    suppressed_thread_ids: frozenset[str] | None = None
    #: Memoised per-request: contact matches keyed by the message's address set, and inherited
    #: thread mappings keyed by thread. Both repeat heavily inside one conversation — the same
    #: two people, the same thread — so this is where the two-query lookups collapse.
    contacts_by_addresses: dict[tuple[str, ...], Any] = field(default_factory=dict)
    mappings_by_thread: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkipExplanation:
    """A decision, plus the two things only the *caller* can know.

    ``before_connection`` and ``never_offered`` are not gates — the chain never ran for these
    messages, so running it and reporting a verdict would be a confident lie. They are answered
    by comparing the message against the connection rather than against the rules, and they
    matter because between them they cover every "it is older than the integration" and every
    resync gap: the two cases where the honest answer is *"this was never offered"* and the
    right response is simply to import it.
    """

    reason: SkipReason | None = None
    detail: dict[str, str] = field(default_factory=dict)
    #: Sent before this mailbox was connected. The first poll baselines and imports nothing, on
    #: purpose — connecting a mailbox is opt-in going forward, never a retroactive import.
    before_connection: bool = False
    #: Every gate passes and there is still no row: the poller never saw it. A `historyId` gap
    #: (Gmail keeps about a week, and a resync re-baselines) is the ordinary cause, and it is
    #: unknowable per message — which is exactly why this is phrased as what we *observe*
    #: rather than as a cause we would be guessing at.
    never_offered: bool = False

    @property
    def importable(self) -> bool:
        """Is there anything here for the caller to do? (everything except "it is already ours")"""
        return self.reason not in ALREADY_HERE


def owner_display(name: str | None, email: str | None) -> str:
    """A colleague named for a screen — their name, else the address, else nothing."""
    return (name or email or "").strip()


def skip_row_values(
    reason: SkipReason, *, message_id: str, thread_id: str | None, detail: dict[str, str]
) -> dict[str, Any]:
    """The columns a ``gmail_skips`` row carries, and deliberately no others.

    Ids, a reason and a timestamp. The subject and the participants are *available* right here
    and are left behind on purpose: the content is fetched on demand under the user's own grant
    when they ask about this message, exactly as everywhere else in the module.
    """
    return {
        "gmail_message_id": message_id[:64],
        "gmail_thread_id": (thread_id or None) and thread_id[:64],
        "reason": reason.value,
        "detail": {k: v[:200] for k, v in detail.items()} or None,
    }


def unknown_reason(value: str | None) -> SkipReason | None:
    """A stored reason string back to the enum, tolerating one we have since renamed."""
    if value is None:
        return None
    try:
        return SkipReason(value)
    except ValueError:
        return None


__all__ = [
    "ALREADY_HERE",
    "PERSISTED_REASONS",
    "Decision",
    "GateCache",
    "SkipExplanation",
    "SkipReason",
    "owner_display",
    "skip_row_values",
    "unknown_reason",
]