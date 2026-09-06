"""The Gmail-shaped half of the matching rules — the wire format, nothing about the agency.

Everything about *whose* mail it is, whether it concerns a client and where it files lives in
:mod:`app.core.mailbox.matching`, shared with every other mailbox feed; this module re-exports
those names so the feed reads as one vocabulary, and adds what only Gmail knows: how headers
travel in a ``format=metadata`` payload, what ``labelIds`` say, and how a ``format=full`` body
is encoded.
"""

from __future__ import annotations

import base64
import re
from typing import Any

from app.core.mailbox import matching as core
from app.core.mailbox.matching import (
    ContactMatch,
    clean_snippet,
    has_external_match,
    intended_owner,
    internal_only,
    is_internal_match,
    parse_participants,
    resolve_mappings,
    sender_of,
)
from app.core.mailbox.policy import decide_status

__all__ = [
    "ContactMatch",
    "attachment_parts",
    "clean_snippet",
    "decide_status",
    "direction_of",
    "extract_markdown",
    "extract_text",
    "has_external_match",
    "headers_map",
    "intended_owner",
    "internal_only",
    "is_internal_match",
    "is_relevant",
    "parse_participants",
    "part_content_id",
    "resolve_mappings",
    "sender_of",
]


def headers_map(message: dict[str, Any]) -> dict[str, str]:
    return {
        header.get("name", ""): header.get("value", "")
        for header in (message.get("payload") or {}).get("headers", [])
    }


def direction_of(label_ids: list[str], *, sender_internal: bool = False) -> str:
    """Which way the mail went — ``SENT`` answers it for the sender's own copy, and a ``From``
    that is one of ours answers it for everybody else's (the core rule, Gmail's label)."""
    return core.direction_of(sent_by_mailbox="SENT" in label_ids, sender_internal=sender_internal)


def is_relevant(label_ids: list[str], excluded_label_id: str | None) -> bool:
    """Drafts, spam and trash never log; neither does the owner's opt-out label."""
    labels = set(label_ids)
    if labels & {"DRAFT", "SPAM", "TRASH"}:
        return False
    return not (excluded_label_id and excluded_label_id in labels)


# --------------------------------------------------------------------------- #
# Body extraction (format=full payloads)
# --------------------------------------------------------------------------- #
def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode(
        "utf-8", errors="replace"
    )


def _walk_parts(part: dict[str, Any], mime: str) -> str | None:
    if part.get("mimeType") == mime and (part.get("body") or {}).get("data"):
        return _decode(part["body"]["data"])
    for child in part.get("parts") or []:
        found = _walk_parts(child, mime)
        if found is not None:
            return found
    return None


def attachment_parts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """The MIME parts worth fetching (#180): a filename **or** a body reference, plus an
    ``attachmentId`` to fetch the bytes by. Inline text/html parts have neither and stay out.

    A part carrying a ``Content-ID`` is a candidate either way — whether it is *content of the
    body* or an ordinary attachment is decided by :func:`part_content_id` against what the
    converted body actually references, not by whether the sending client gave it a filename.
    """
    found: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        body = part.get("body") or {}
        if body.get("attachmentId") and (part.get("filename") or part_content_id(part)):
            found.append(part)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    return found


def part_content_id(part: dict[str, Any]) -> str | None:
    """A part's ``Content-ID`` as the body spells it: ``<x@y>`` in the header, ``cid:x@y``."""
    for header in part.get("headers") or []:
        if (header.get("name") or "").lower() == "content-id":
            return (header.get("value") or "").strip().strip("<>") or None
    return None


def extract_markdown(payload: dict[str, Any]) -> str | None:
    """The message's ``text/html`` part converted to markdown, or ``None`` when it has none.

    Deliberately the HTML part even when a ``text/plain`` alternative exists: both say the
    same words, and only one of them still knows it had a list in it. The plain part remains
    what :func:`extract_text` returns and what search reads.
    """
    from app.core.htmlmd import html_to_markdown

    return html_to_markdown(_walk_parts(payload, "text/html"))


def extract_text(payload: dict[str, Any]) -> str | None:
    """The message body as plain text: the ``text/plain`` part, else stripped ``text/html``."""
    plain = _walk_parts(payload, "text/plain")
    if plain is not None:
        return plain.strip() or None
    return core.html_to_text(_walk_parts(payload, "text/html"))


#: Kept importable for the search builder, which quotes what it is handed.
_search_token = core.search_token
_TAG_RE = re.compile(r"<[^>]+>")
