"""Why one email was, or was not, logged — Outlook's reading of the shared vocabulary.

The decision itself (:class:`~app.core.mailbox.gates.SkipReason`, ``Decision``, ``GateCache``,
``SkipExplanation``) lives in :mod:`app.core.mailbox.gates`, because the Gmail feed declines a
message for the same reasons and the web draws one set of sentences for both. What stays here is
the one thing that is Outlook's: the columns a persisted ``outlook_skips`` row carries.
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
    reason: SkipReason, *, message_id: str, conversation_id: str | None, detail: dict[str, str]
) -> dict[str, Any]:
    """The columns an ``outlook_skips`` row carries, and deliberately no others: ids, a reason,
    and the one or two short strings that make it actionable. Never the subject, never the
    participants — the content is fetched on demand under the user's own grant."""
    return {
        "message_id": message_id[:512],
        "conversation_id": (conversation_id or None) and conversation_id[:512],
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
