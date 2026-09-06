"""The Graph-shaped half of the matching rules — the wire format, nothing about the agency.

Everything about *whose* mail it is, whether it concerns a client and where it files lives in
:mod:`app.core.mailbox.matching`, shared with the Gmail feed; this module re-exports those names
so the feed reads as one vocabulary and adds what only Graph knows: how a ``message`` resource
spells its participants, its dates, its folder and its categories.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.mailbox import matching as core
from app.core.mailbox.matching import (
    ContactMatch,
    clean_snippet,
    has_external_match,
    html_to_text,
    intended_owner,
    internal_only,
    is_internal_match,
    participants_from_addresses,
    resolve_mappings,
    search_token,
    sender_of,
)
from app.core.mailbox.policy import decide_status

__all__ = [
    "ContactMatch",
    "WellKnownFolders",
    "clean_snippet",
    "decide_status",
    "direction_of",
    "excluded_category_on",
    "has_external_match",
    "html_to_text",
    "intended_owner",
    "internal_only",
    "is_internal_match",
    "is_not_a_message",
    "occurred_at_of",
    "participants_of",
    "participants_from_addresses",
    "resolve_mappings",
    "rfc822_id_of",
    "search_token",
    "sender_of",
]


@dataclass(frozen=True)
class WellKnownFolders:
    """The four folder ids a poll needs to read a message's *state* off ``parentFolderId``.

    Graph has no label vocabulary: a draft, a junked mail and a binned one are ordinary
    messages in special folders, and "sent by this mailbox" is membership of Sent Items. The
    ids are per-mailbox, so they are resolved once per poll and never stored.
    """

    junk: str | None = None
    deleted: str | None = None
    drafts: str | None = None
    sent: str | None = None


def _pair(entry: dict[str, Any] | None) -> tuple[str | None, str | None] | None:
    address = ((entry or {}).get("emailAddress") or {}) if entry else {}
    if not address.get("address"):
        return None
    return (address.get("name") or None, address["address"])


def participants_of(message: dict[str, Any]) -> list[dict[str, str]]:
    """``[{email, name, role}]`` from ``from`` / ``toRecipients`` / ``ccRecipients``."""
    return participants_from_addresses(
        sender=_pair(message.get("from")),
        to=[p for p in (_pair(r) for r in message.get("toRecipients") or []) if p],
        cc=[p for p in (_pair(r) for r in message.get("ccRecipients") or []) if p],
    )


def rfc822_id_of(message: dict[str, Any]) -> str | None:
    """The global ``Message-ID``, exactly as Graph reports it — angle brackets included, which
    is also how the Gmail feed stores the raw header, so the cross-mailbox dedup compares
    like with like."""
    return (message.get("internetMessageId") or "").strip()[:512] or None


def occurred_at_of(message: dict[str, Any]) -> datetime:
    """When it happened: received, else sent, else now (a manual import of an old message)."""
    for key in ("receivedDateTime", "sentDateTime"):
        raw = message.get(key)
        if raw:
            parsed = parse_graph_datetime(raw)
            if parsed is not None:
                return parsed
    return datetime.now(UTC)


def parse_graph_datetime(raw: str) -> datetime | None:
    """Graph's ``2026-07-12T10:00:00Z`` (sometimes with seven fractional digits) → aware UTC."""
    text = raw.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Python parses up to six fractional digits; Graph occasionally prints seven.
    if "." in text:
        head, tail = text.split(".", 1)
        digits = ""
        rest = tail
        while rest and rest[0].isdigit():
            digits += rest[0]
            rest = rest[1:]
        text = f"{head}.{digits[:6]}{rest}" if digits else f"{head}{rest}"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def is_not_a_message(message: dict[str, Any], folders: WellKnownFolders) -> bool:
    """A draft, or anything in Junk or Deleted Items — never a message that was sent or received."""
    if message.get("isDraft"):
        return True
    folder = message.get("parentFolderId")
    return bool(folder) and folder in {folders.junk, folders.deleted, folders.drafts}


def excluded_category_on(message: dict[str, Any], excluded: str | None) -> str | None:
    """The owner's opt-out category as it appears on the message, or ``None``.

    Compared case-insensitively: Outlook shows categories as typed but matches them loosely,
    and an owner who typed "Geen-CRM" on the account page means the "geen-crm" they tagged.
    """
    if not excluded:
        return None
    wanted = excluded.strip().lower()
    for category in message.get("categories") or []:
        if isinstance(category, str) and category.strip().lower() == wanted:
            return category
    return None


def direction_of(
    message: dict[str, Any], folders: WellKnownFolders, *, sender_internal: bool = False
) -> str:
    """Sent Items answers it for the sender's own copy; a ``From`` that is one of ours answers
    it for everybody else's (the core rule, Graph's folder)."""
    sent = bool(folders.sent) and message.get("parentFolderId") == folders.sent
    return core.direction_of(sent_by_mailbox=sent, sender_internal=sender_internal)
