"""Why one email was, or was not, logged — Gmail's reading of the shared vocabulary.

The decision itself (:class:`~app.core.mailbox.gates.SkipReason`, ``Decision``, ``GateCache``,
``SkipExplanation``) lives in :mod:`app.core.mailbox.gates`, because a feed built on Outlook
declines a message for the same reasons and the web draws one set of sentences for both. What
stays here is the one thing that is Gmail's: the columns a persisted ``gmail_skips`` row carries.
The reasoning behind the vocabulary — a dry run rather than a log, two persisted exceptions —
is written on the core module and not restated.
"""

from __future__ import annotations

from typing import Any

from app.core.mailbox.gates import (
    ALREADY_HERE,
    PERSISTED_REASONS,
    Decision,
    GateCache,
    SkipExplanation,
    SkipReason,
    owner_display,
    unknown_reason,
)


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
