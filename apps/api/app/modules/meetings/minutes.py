"""The minutes: one transcript into a summary, the decisions and the action items.

The posture is the dictation's (#382, ``core/ai/taskdraft.py``), not the e-mail enrichment's
(#327): a colleague pressed record, a colleague reads the draft, and nothing is written until
they confirm — so the vocabulary is the whole minutes document. Three things stay exactly as
strict as everywhere else, and the third is new.

**An id is grounded in the shortlist.** ``assignee_user_id`` must be a staff id the model was
shown (``candidates.gather``'s members block); anything else is dropped and the item keeps its
``owner_label`` — a misheard name comes back as *nobody assigned*, never as a colleague who was
not in the room.

**A date is bounded.** A due date resolves against the org's calendar, which the prompt states
with its weekdays (``intake_ai.calendar_line``'s lesson: weekday arithmetic is ours), inside the
same window every model-read date gets.

**A claim quotes its evidence, and the evidence is checked.** Every decision and action item
carries the words it was drawn from and where in the recording they fall. The quote is looked
up in the transcript after normalisation — case, whitespace, punctuation — and an item whose
quote is not there is **kept and marked** ``verified: false`` rather than dropped: a reviewer
can judge a paraphrase in a second, and a dropped item is a decision nobody sees. What the check
buys is precise: it stops the model *inventing* an agreement, which is the failure a reader
cannot catch from the summary alone.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from typing import Any

from app.core.ai import prompts
from app.core.ai.candidates import ParseCandidates
from app.core.ai.prompts import calendar_line
from app.core.ai.providers import ChatMessage, ToolDef
from app.core.ai.service import AIService
from app.modules.meetings.schemas import (
    MinutesActionItem,
    MinutesDecision,
    MinutesDraft,
    MinutesTopic,
)

logger = logging.getLogger("schakl.meetings")

FEATURE = "meeting_assist"

#: One free round, then a forced submit — the dictation's shape.
_MAX_ROUNDS = 2
#: A meeting's transcript is the largest document any feature here sends. Two hours of Dutch is
#: ~30k tokens and fits every provider; a whole day would not, so the document is cut here and
#: the draft says so (``partial_input``) rather than the request failing with nothing to show.
MAX_TRANSCRIPT_CHARS = 350_000
_DUE_PAST_DAYS = 7
_DUE_FUTURE_DAYS = 730
_QUOTE_CHARS = 500

SUBMIT_MINUTES = ToolDef(
    name="submit_minutes",
    description="Submit the minutes of the meeting. Call exactly once, as your final act.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": ["string", "null"],
                "description": "What the meeting was about, in a handful of words, if the "
                "given title is a placeholder. Null to keep the given title.",
            },
            "summary": {
                "type": "string",
                "description": "Three to eight sentences: why the meeting was held and what "
                "came out of it. Markdown. Never a list of everything said.",
            },
            "topics": {
                "type": "array",
                "maxItems": 40,
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "text": {
                            "type": "string",
                            "description": "What was discussed under this heading, in a "
                            "short paragraph or a few bullets. Markdown.",
                        },
                    },
                    "required": ["heading", "text"],
                    "additionalProperties": False,
                },
            },
            "decisions": {
                "type": "array",
                "maxItems": 60,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "What was agreed, as one sentence.",
                        },
                        "quote": {
                            "type": "string",
                            "description": "The exact words from the transcript this rests "
                            "on — copied verbatim, up to a sentence or two.",
                        },
                        "at": {
                            "type": ["number", "null"],
                            "description": "Seconds into the recording where it was said.",
                        },
                    },
                    "required": ["text", "quote"],
                    "additionalProperties": False,
                },
            },
            "action_items": {
                "type": "array",
                "maxItems": 60,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "The task, in a few words."},
                        "description": {"type": ["string", "null"]},
                        "assignee_user_id": {
                            "type": ["string", "null"],
                            "description": "The staff member who took it on, from the STAFF "
                            "list, by id. Null when it is not one of them.",
                        },
                        "owner_label": {
                            "type": ["string", "null"],
                            "description": "Who took it on when it is not a staff member — "
                            "the client's name as spoken.",
                        },
                        "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
                        "quote": {"type": "string"},
                        "at": {"type": ["number", "null"]},
                    },
                    "required": ["title", "quote"],
                    "additionalProperties": False,
                },
            },
            "open_questions": {
                "type": "array",
                "maxItems": 40,
                "items": {"type": "string"},
                "description": "What was raised and left unanswered.",
            },
        },
        "required": ["summary"],
        "additionalProperties": False,
    },
)


def system_prompt(
    *,
    today: date,
    now: datetime | None,
    locale: str,
    agency: str,
    staff: str,
    speakers: dict[str, str],
) -> str:
    """The minutes prompt. Written for a transcript: long, unpunctuated in places, with the
    recogniser's guess at every name and a speaker label instead of a person."""
    named = ", ".join(f"{label} = {name}" for label, name in speakers.items() if name)
    parts = [
        f"You write the minutes of a meeting for {agency}, an agency, from a transcript of "
        "the recording. You create nothing yourself — you submit one draft that a colleague "
        "who was in the meeting reads, corrects and confirms.",
        calendar_line(today, now),
        f"Write every sentence you produce in {prompts.language_name(locale)}, whatever "
        "language was spoken.",
        "The transcript was produced by a speech recogniser. Names are its guess and may be "
        "wrong; speaker labels (S1, S2 …) are positions, not people"
        + (f" — except these, which the reviewer named: {named}." if named else ".")
        + " Where a speaker introduces themselves or is addressed by name, use that name.",
        # Point of view: the e-mail enrichment's lesson (docs/AI.md, "Whose task it is").
        f"Point of view: the minutes are {agency}'s. An action item is something a named "
        "person committed to do. When it is one of the agency's staff, set assignee_user_id "
        "from the STAFF list; when it is the client or a third party, leave assignee_user_id "
        "null and put their name in owner_label. Never assign the client's promise to a "
        "colleague.",
        "Rules:\n"
        "- A decision is something the participants agreed, not something one of them "
        "proposed. An action item has an owner or a clear 'we'. What was merely discussed "
        "goes under topics; what was raised and not settled under open_questions.\n"
        "- Every decision and action item carries a quote: the transcript's own words, copied "
        "verbatim, that the item rests on. Never paraphrase a quote. An item you cannot quote "
        "is an item that was not said — leave it out.\n"
        "- A due date only when a date or a weekday was actually spoken, resolved against the "
        "calendar above. Never invent one.\n"
        "- Do not restate the whole meeting in the summary, do not list who attended, and do "
        "not write that something was not discussed.",
    ]
    if staff:
        parts.append(
            "STAFF (id\tname) — the agency's own people, the only valid values for "
            f"assignee_user_id, copied character for character:\n{staff}"
        )
    parts.append(prompts._INJECTION_STANCE)  # noqa: SLF001 — the shared stance, on purpose
    return "\n\n".join(parts)


def transcript_document(
    *,
    title: str,
    occurred_at: datetime,
    kind: str,
    segments: list[dict[str, Any]],
    text: str | None,
) -> tuple[str, bool]:
    """The transcript as data inside JSON — never as instructions (``_INJECTION_STANCE``).

    Returns the document and whether it was cut to :data:`MAX_TRANSCRIPT_CHARS`.
    """
    lines: list[dict[str, Any]] = []
    used = 0
    cut = False
    if segments:
        for seg in segments:
            entry = {
                "at": round(float(seg.get("start") or 0), 1),
                "speaker": seg.get("speaker"),
                "text": seg.get("text") or "",
            }
            used += len(entry["text"]) + 24
            if used > MAX_TRANSCRIPT_CHARS:
                cut = True
                break
            lines.append(entry)
    else:
        flat = text or ""
        if len(flat) > MAX_TRANSCRIPT_CHARS:
            flat = flat[:MAX_TRANSCRIPT_CHARS]
            cut = True
        lines.append({"at": 0, "speaker": None, "text": flat})
    document = {
        "meeting": {"title": title, "occurred_at": occurred_at.isoformat(), "kind": kind},
        "transcript": lines,
        "transcript_cut_short": cut,
    }
    return json.dumps(document, ensure_ascii=False), cut


_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)


def normalise(text: str) -> str:
    """Case, accents, punctuation and whitespace folded — the only equality a quote can be
    held to when both sides passed through a recogniser and a model."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = _PUNCT.sub(" ", folded.lower())
    return _WS.sub(" ", folded).strip()


def quote_found(quote: str | None, haystack: str) -> bool:
    """Is the model's quote in the transcript? A short quote does not count as evidence:
    three words match by accident somewhere in two hours of talk."""
    if not quote:
        return False
    needle = normalise(quote)
    words = needle.split()
    if len(words) < 4:
        return False
    if needle in haystack:
        return True
    # A long quote that straddles a segment boundary or lost a filler word: accept when the
    # first and last handful of words are both there, in order.
    if len(words) >= 12:
        head, tail = " ".join(words[:5]), " ".join(words[-5:])
        i = haystack.find(head)
        return i >= 0 and haystack.find(tail, i) >= 0
    return False


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _seconds(value: Any, *, duration: int | None) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    seconds = float(value)
    if seconds < 0:
        return None
    if duration and seconds > duration + 5:
        return None
    return round(seconds, 1)


def _due(value: Any, *, today: date) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None
    if today - timedelta(days=_DUE_PAST_DAYS) <= parsed <= today + timedelta(days=_DUE_FUTURE_DAYS):
        return parsed
    return None


def _staff_id(value: Any, allowed: set[str]) -> uuid.UUID | None:
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def draft_from_call(
    submitted: dict[str, Any],
    *,
    transcript_text: str,
    staff_ids: set[str],
    today: date,
    duration: int | None,
) -> MinutesDraft:
    """Every field re-derived from the model's one call; nothing passed through."""
    haystack = normalise(transcript_text)

    topics: list[MinutesTopic] = []
    raw_topics = submitted.get("topics")
    if isinstance(raw_topics, list):
        for entry in raw_topics[:40]:
            if not isinstance(entry, dict):
                continue
            heading = _text(entry.get("heading"), 255)
            body = _text(entry.get("text"), 8000)
            if heading and body:
                topics.append(MinutesTopic(heading=heading, text=body))

    decisions: list[MinutesDecision] = []
    raw_decisions = submitted.get("decisions")
    if isinstance(raw_decisions, list):
        for entry in raw_decisions[:60]:
            if not isinstance(entry, dict):
                continue
            text = _text(entry.get("text"), 2000)
            if not text:
                continue
            quote = _text(entry.get("quote"), _QUOTE_CHARS)
            decisions.append(
                MinutesDecision(
                    text=text,
                    quote=quote,
                    at=_seconds(entry.get("at"), duration=duration),
                    verified=quote_found(quote, haystack),
                )
            )

    items: list[MinutesActionItem] = []
    raw_items = submitted.get("action_items")
    if isinstance(raw_items, list):
        for entry in raw_items[:60]:
            if not isinstance(entry, dict):
                continue
            title = _text(entry.get("title"), 512)
            if not title:
                continue
            quote = _text(entry.get("quote"), _QUOTE_CHARS)
            assignee = _staff_id(entry.get("assignee_user_id"), staff_ids)
            items.append(
                MinutesActionItem(
                    title=title,
                    description=_text(entry.get("description"), 4000),
                    assignee_user_id=assignee,
                    owner_label=_text(entry.get("owner_label"), 255),
                    due_date=_due(entry.get("due_date"), today=today),
                    at=_seconds(entry.get("at"), duration=duration),
                    quote=quote,
                    verified=quote_found(quote, haystack),
                    # A client's promise is minuted, not put on our board; the reviewer may
                    # still tick it (someone has to chase it).
                    create_task=assignee is not None,
                )
            )

    questions: list[str] = []
    raw_questions = submitted.get("open_questions")
    if isinstance(raw_questions, list):
        for entry in raw_questions[:40]:
            text = _text(entry, 1000)
            if text:
                questions.append(text)

    return MinutesDraft(
        title=_text(submitted.get("title"), 255),
        summary=_text(submitted.get("summary"), 8000) or "",
        topics=topics,
        decisions=decisions,
        action_items=items,
        open_questions=questions,
    )


async def draft_minutes(
    service: AIService,
    *,
    title: str,
    occurred_at: datetime,
    kind: str,
    segments: list[dict[str, Any]],
    transcript_text: str,
    speakers: dict[str, str],
    candidates: ParseCandidates,
    today: date,
    now: datetime | None,
    locale: str,
    agency: str,
    duration: int | None,
) -> MinutesDraft:
    """One transcript into one draft. Raises ``AppError`` on a provider failure (the caller —
    the worker — turns it into the row's ``failed`` state)."""
    staff = "\n".join(f"{m['id']}\t{m['name']}" for m in candidates.members)
    system = system_prompt(
        today=today, now=now, locale=locale, agency=agency, staff=staff, speakers=speakers
    )
    document, cut = transcript_document(
        title=title, occurred_at=occurred_at, kind=kind, segments=segments, text=transcript_text
    )
    submitted: dict[str, Any] = {}
    truncated = False
    history: list[ChatMessage] = [ChatMessage(role="user", content=document)]
    try:
        for round_no in range(_MAX_ROUNDS):
            force = SUBMIT_MINUTES.name if round_no == _MAX_ROUNDS - 1 else None
            text, calls = await service.complete(
                FEATURE, system=system, messages=history, tools=[SUBMIT_MINUTES], force_tool=force
            )
            call = next((c for c in calls if c.name == SUBMIT_MINUTES.name), None)
            if call is not None:
                truncated = call.incomplete or service.truncated
                submitted = call.input
                break
            if not calls:
                break
            history.append(ChatMessage(role="assistant", content=text, tool_calls=tuple(calls)))
    finally:
        await service.flush_usage(FEATURE)
    draft = draft_from_call(
        submitted,
        transcript_text=transcript_text,
        staff_ids=candidates.member_ids(),
        today=today,
        duration=duration,
    )
    draft.truncated = truncated
    draft.partial_input = cut
    return draft


__all__ = [
    "FEATURE",
    "MAX_TRANSCRIPT_CHARS",
    "SUBMIT_MINUTES",
    "draft_from_call",
    "draft_minutes",
    "normalise",
    "quote_found",
    "system_prompt",
    "transcript_document",
]
