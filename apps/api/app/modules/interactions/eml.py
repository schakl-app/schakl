"""Parse an uploaded ``.eml`` into the shape the gmail feed already produces (issue #262).

Pure functions, no I/O — the rules are unit-testable, exactly like the google module's
``matching.py``. The two deliberately do **not** share code: ``matching`` lives in the
licensed ``google`` module and interactions must never import it (dependency direction is
google → interactions, CLAUDE.md §6). What is shared is the *shape* — subject, participants,
``occurred_at``, body text, ``rfc822_message_id``, attachments — so a manually uploaded email
and a synced one render identically.

Everything comes out of Python's stdlib ``email`` package with ``policy.default``: it decodes
RFC 2047 headers, picks charsets per part, and hands back an ``EmailMessage`` that knows its
own body and attachments. No new dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from html import unescape

#: Roles, in the order the participant list should read.
_HEADER_ROLES = (("From", "from"), ("To", "to"), ("Cc", "cc"))

#: At least one of these must be present for the bytes to be an email at all — a renamed
#: `.zip` parses into a message with no headers and the whole file as its "body", which would
#: otherwise land on the timeline as an empty contactmoment.
_EVIDENCE_HEADERS = ("From", "To", "Cc", "Subject", "Date", "Message-ID")

_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
_WS_RE = re.compile(r"\s+")

#: How much of the body the timeline preview shows, matching a gmail snippet's order of size.
SNIPPET_CHARS = 200


#: What a ``.eml`` may call itself. The **extension** is the reliable signal: browsers and
#: operating systems label exported mail inconsistently — Outlook's export commonly arrives as
#: ``application/octet-stream``, Thunderbird's as ``message/rfc822`` — so the reported
#: content type is accepted as an alternative, never required (#262).
#: Outlook's ``.msg`` is deliberately absent: it is a binary OLE container, not RFC 5322, and
#: the stdlib cannot read it — accepting it here would trade one clear refusal for a confusing
#: "not an email" after the upload. Outlook can "Save as" ``.eml``.
EML_SUFFIXES = (".eml",)
EML_CONTENT_TYPES = frozenset({"message/rfc822", "message/global"})


class EmlParseError(ValueError):
    """The bytes are not a parseable email — the caller turns this into a 422."""


def looks_like_eml(filename: str | None, content_type: str | None) -> bool:
    """Is this upload plausibly an exported email? Extension **or** content type is enough."""
    name = (filename or "").strip().lower()
    if name.endswith(EML_SUFFIXES):
        return True
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    return declared in EML_CONTENT_TYPES


@dataclass(frozen=True)
class EmlAttachment:
    filename: str
    content_type: str
    data: bytes


@dataclass(frozen=True)
class ParsedEml:
    """One uploaded message, in the field shape ``interactions`` stores."""

    subject: str | None = None
    #: ``None`` when the message carries no usable ``Date`` header; the caller decides what
    #: to fall back to (the upload moment) rather than this parser inventing a time.
    occurred_at: datetime | None = None
    #: ``[{email, name, role}]`` — the same JSONB shape gmail rows carry.
    participants: list[dict[str, str | None]] = field(default_factory=list)
    body_text: str | None = None
    snippet: str | None = None
    rfc822_message_id: str | None = None
    #: The sender's address, lowercased — what decides inbound vs outbound.
    from_email: str | None = None
    attachments: list[EmlAttachment] = field(default_factory=list)


def parse_eml(data: bytes) -> ParsedEml:
    """Parse raw ``.eml`` bytes. Raises :class:`EmlParseError` when they are not an email."""
    try:
        message = BytesParser(policy=policy.default).parsebytes(data)
    except Exception as exc:  # noqa: BLE001 — any parser failure is one 422 to the uploader
        raise EmlParseError("unparseable message") from exc
    if not isinstance(message, EmailMessage):  # pragma: no cover — policy.default guarantees it
        raise EmlParseError("unexpected message type")
    if not any(name in message for name in _EVIDENCE_HEADERS):
        raise EmlParseError("no email headers")
    body_text = _body_text(message)
    return ParsedEml(
        subject=_header(message, "Subject"),
        occurred_at=_occurred_at(message),
        participants=_participants(message),
        body_text=body_text,
        snippet=_snippet(body_text),
        rfc822_message_id=(_header(message, "Message-ID") or None),
        from_email=_first_address(message, "From"),
        attachments=_attachments(message),
    )


def _header(message: EmailMessage, name: str) -> str | None:
    """A header as plain text — ``policy.default`` has already un-encoded RFC 2047 words."""
    try:
        raw = message[name]
    except Exception:  # noqa: BLE001 — a malformed header must not lose the whole message
        return None
    if raw is None:
        return None
    value = str(raw).strip()
    return value or None


def _occurred_at(message: EmailMessage) -> datetime | None:
    """The ``Date`` header as an instant. ``-0000`` (zone unknown) is read as UTC, which is
    what RFC 5322 means by it — never the tenant's wall clock, which would move the email."""
    raw = _header(message, "Date")
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _addresses(message: EmailMessage, name: str) -> list[tuple[str, str]]:
    try:
        raw = [str(value) for value in message.get_all(name, [])]
    except Exception:  # noqa: BLE001 — a broken address header is skipped, not fatal
        return []
    return [
        (display_name, address.lower())
        for display_name, address in getaddresses(raw)
        if address and "@" in address
    ]


def _first_address(message: EmailMessage, name: str) -> str | None:
    found = _addresses(message, name)
    return found[0][1] if found else None


def _participants(message: EmailMessage) -> list[dict[str, str | None]]:
    """``[{email, name, role}]`` from From/To/Cc, addresses lowercased and de-duplicated."""
    participants: list[dict[str, str | None]] = []
    seen: set[tuple[str, str]] = set()
    for header_name, role in _HEADER_ROLES:
        for display_name, address in _addresses(message, header_name):
            if (address, role) in seen:
                continue
            seen.add((address, role))
            participants.append({"email": address, "name": display_name or None, "role": role})
    return participants


def _part_text(part: EmailMessage) -> str | None:
    """One text part's content, whatever it claims about its charset."""
    try:
        content = part.get_content()
    except (LookupError, UnicodeError, KeyError, ValueError):
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return None
        content = payload.decode("utf-8", errors="replace")
    return content if isinstance(content, str) else None


def _body_text(message: EmailMessage) -> str | None:
    """The message body as plain text: ``text/plain`` if there is one, else stripped HTML —
    the same preference (and the same stripping) the gmail feed's ``extract_text`` applies,
    so a manually uploaded email and a synced one read the same."""
    try:
        part = message.get_body(preferencelist=("plain", "html"))
    except Exception:  # noqa: BLE001 — a malformed structure degrades to "no body"
        part = None
    if part is None:
        return None
    content = _part_text(part)
    if content is None:
        return None
    if part.get_content_type() != "text/html":
        return content.strip() or None
    return html_to_text(content)


def html_to_text(html: str) -> str:
    """Strip an HTML body down to readable text (script/style dropped, entities unescaped)."""
    text = unescape(_TAG_RE.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RE.sub("\n\n", text).strip()


def _snippet(body_text: str | None) -> str | None:
    """The preview the timeline row shows — gmail rows get one from the API, so an uploaded
    email builds its own from the opening of the body."""
    if not body_text:
        return None
    collapsed = _WS_RE.sub(" ", body_text).strip()
    if not collapsed:
        return None
    if len(collapsed) <= SNIPPET_CHARS:
        return collapsed
    return collapsed[:SNIPPET_CHARS].rstrip() + "…"


def _attachments(message: EmailMessage) -> list[EmlAttachment]:
    """The parts that are real attachments: a filename plus decodable bytes. Inline body
    parts carry no filename and stay out, mirroring the gmail feed's ``attachment_parts``.
    A nested ``message/rfc822`` decodes to no bytes and is skipped rather than half-stored."""
    found: list[EmlAttachment] = []
    try:
        parts = list(message.iter_attachments())
    except Exception:  # noqa: BLE001 — a malformed MIME tree still yields its message body
        return found
    for part in parts:
        filename = part.get_filename()
        if not filename:
            continue
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes) or not payload:
            continue
        found.append(
            EmlAttachment(
                filename=str(filename),
                content_type=part.get_content_type() or "application/octet-stream",
                data=payload,
            )
        )
    return found
