"""Pure matching logic for the gmail feed — no I/O, so the rules are unit-testable.

The pipeline decides, for one fetched message: who is on it, whether it is CRM-relevant
(matched to a known contact, not colleague-to-colleague chatter), which records it maps to,
and whether it may be logged with content immediately or waits for the owner's approval.
"""

from __future__ import annotations

import base64
import re
import uuid
from dataclasses import dataclass, field
from email.utils import getaddresses
from html import unescape
from typing import Any

from app.modules.google.models import GmailApprovalMode, GmailThreadFollowup

_HEADER_ROLES = (("From", "from"), ("To", "to"), ("Cc", "cc"))


def headers_map(message: dict[str, Any]) -> dict[str, str]:
    return {
        header.get("name", ""): header.get("value", "")
        for header in (message.get("payload") or {}).get("headers", [])
    }


def parse_participants(headers: dict[str, str]) -> list[dict[str, str]]:
    """``[{email, name, role}]`` from the From/To/Cc headers, addresses lowercased."""
    participants: list[dict[str, str]] = []
    for header_name, role in _HEADER_ROLES:
        raw = headers.get(header_name)
        if not raw:
            continue
        for name, address in getaddresses([raw]):
            if not address or "@" not in address:
                continue
            participants.append(
                {"email": address.lower(), "name": name or None, "role": role}  # type: ignore[dict-item]
            )
    return participants


def direction_of(label_ids: list[str]) -> str:
    return "outbound" if "SENT" in label_ids else "inbound"


def is_relevant(label_ids: list[str], excluded_label_id: str | None) -> bool:
    """Drafts, spam and trash never log; neither does the owner's opt-out label."""
    labels = set(label_ids)
    if labels & {"DRAFT", "SPAM", "TRASH"}:
        return False
    return not (excluded_label_id and excluded_label_id in labels)


@dataclass
class ContactMatch:
    contact_id: uuid.UUID
    #: The contact's companies, oldest link first (deterministic tie-breaking).
    company_ids: list[uuid.UUID] = field(default_factory=list)


def internal_only(participants: list[dict[str, str]], member_emails: set[str]) -> bool:
    """Colleague-to-colleague mail is not a client touchpoint — skip it entirely."""
    addresses = {p["email"] for p in participants}
    return bool(addresses) and addresses <= member_emails


def resolve_mappings(matches: list[ContactMatch]) -> dict[str, uuid.UUID | None]:
    """Contact + company for the interaction row.

    The company is the single unambiguous one when the matched contacts agree; otherwise the
    first (oldest-linked) — every logged email lands on *some* client timeline, reachable and
    remappable, rather than floating unmapped where no panel would ever show it.
    """
    if not matches:
        return {}
    contact_id = matches[0].contact_id if len(matches) == 1 else None
    all_companies: list[uuid.UUID] = []
    for match in matches:
        for company_id in match.company_ids:
            if company_id not in all_companies:
                all_companies.append(company_id)
    company_id = all_companies[0] if all_companies else None
    if contact_id is None and company_id is not None:
        # Several matched contacts: attribute to the one linked to the chosen company.
        for match in matches:
            if company_id in match.company_ids:
                contact_id = match.contact_id
                break
    return {"contact_id": contact_id, "company_id": company_id}


def decide_status(
    approval_mode: str,
    thread_followup: str,
    *,
    inherited: bool,
) -> bool:
    """``True`` = pending (owner approval required before content is shared)."""
    if approval_mode == GmailApprovalMode.AUTO_APPROVE.value:
        return False
    if inherited and thread_followup == GmailThreadFollowup.INHERIT_APPROVE.value:
        return False
    return True


# --------------------------------------------------------------------------- #
# Body extraction (format=full payloads)
# --------------------------------------------------------------------------- #
_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\n{3,}")
#: Zero-width joiners, BOMs, soft hyphens, bidi marks — a marketing preheader's invisible
#: padding, which Gmail happily includes in the snippet it hands us.
_INVISIBLE_RE = re.compile("[\u00ad\u200b-\u200f\u2028\u2029\u202a-\u202e\u2060\ufeff]")


def clean_snippet(raw: str | None) -> str | None:
    """Gmail's ``snippet``, made readable (#263).

    It arrives **HTML-escaped** (``&#39;``, ``&amp;``, ``&nbsp;``) and padded with the
    message's invisible preheader, so stored raw it reads as escape codes in every list
    row — and matches nothing when someone searches the words they actually saw. Decoded
    and single-spaced once here, at the seam, not in each of the surfaces that show it.
    """
    if not raw:
        return None
    return " ".join(_INVISIBLE_RE.sub("", unescape(raw)).split()) or None


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
    """The MIME parts that are real attachments (#180): a filename plus an ``attachmentId``
    to fetch the bytes by. Inline text/html parts carry no filename and stay out."""
    found: list[dict[str, Any]] = []

    def walk(part: dict[str, Any]) -> None:
        body = part.get("body") or {}
        if part.get("filename") and body.get("attachmentId"):
            found.append(part)
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    return found


def extract_text(payload: dict[str, Any]) -> str | None:
    """The message body as plain text: the ``text/plain`` part, else stripped ``text/html``."""
    plain = _walk_parts(payload, "text/plain")
    if plain is not None:
        return plain.strip() or None
    html = _walk_parts(payload, "text/html")
    if html is None:
        return None
    text = unescape(_TAG_RE.sub(" ", re.sub(r"(?is)<(script|style).*?</\1>", " ", html)))
    text = "\n".join(line.strip() for line in text.splitlines())
    return _BLANK_RE.sub("\n\n", text).strip() or None
