"""Executable AI tools the meetings module contributes (CLAUDE.md §6, §12).

Three read-only tools on the ``mcp_tools`` seam — the in-app assistant executes them and the
MCP server serves the same catalog to an external agent beside the generated route tools.
Each carries the read permission its service demands, so a caller who may not open a meeting
never sees the tool (``ctx.can`` filters the offer) and is refused by the service if they call
it anyway (``ctx.require`` on the read) — both, because the filter alone is cosmetic and the
check alone leaves a control drawn that can only refuse (#253).

* ``meetings.find`` — the register, narrowed by client, status or a search over titles *and*
  transcripts, so "what did we agree with Nova about the homepage" resolves to a meeting.
* ``meetings.transcript`` — the words whole, every speaker label resolved to a person, with the
  clock beside each line: what an agent reads before it answers anything about a meeting.
* ``meetings.minutes`` — the minutes as confirmed (or as drafted, marked so): summary, topics,
  decisions and action items with who owns each, and where in the recording it rests.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.ai.tools import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.meetings.service import MeetingService, group_action_items

_READ = "meetings.meeting.read"
_FIND_LIMIT = 10


def _meeting_id(args: dict[str, Any]) -> uuid.UUID:
    try:
        return uuid.UUID(str(args.get("meeting_id")))
    except (TypeError, ValueError) as exc:
        raise AppError("validation", "errors.validation", status_code=422) from exc


def _optional_uuid(value: Any) -> uuid.UUID | None:
    if not value:
        return None
    try:
        return uuid.UUID(str(value))
    except ValueError as exc:
        raise AppError("validation", "errors.validation", status_code=422) from exc


async def _find(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    query = args.get("query")
    status = args.get("status")
    page = await MeetingService(ctx).list(
        limit=_FIND_LIMIT,
        offset=0,
        company_id=_optional_uuid(args.get("company_id")),
        status=str(status) if isinstance(status, str) and status.strip() else None,
        q=str(query).strip() if isinstance(query, str) and query.strip() else None,
        count=False,
    )
    rows = page.items
    return ToolResult(
        data={
            "meetings": [
                {
                    "id": str(m.id),
                    "title": m.title,
                    "status": m.status.value,
                    "kind": m.kind.value,
                    "occurred_at": m.occurred_at.isoformat(),
                    "duration_seconds": m.duration_seconds,
                    "company": m.company_name,
                    "company_id": str(m.company_id) if m.company_id else None,
                    "project": m.project_name,
                    "recorded_by": m.owner_name,
                    "decisions": m.decision_count,
                    "action_items": m.action_item_count,
                }
                for m in rows
            ]
        },
        sources=tuple(Source(type="meeting", id=str(m.id), label=m.title) for m in rows),
    )


async def _transcript(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    transcript = await MeetingService(ctx).transcript(_meeting_id(args))
    return ToolResult(
        data={
            "meeting_id": str(transcript.meeting_id),
            "title": transcript.title,
            "occurred_at": transcript.occurred_at.isoformat(),
            "language": transcript.language,
            "model": transcript.model,
            "parts": transcript.parts,
            "diarized": transcript.diarized,
            "speakers": transcript.speakers,
            "segments": [
                {
                    "at": round(s.start, 1),
                    "speaker": transcript.speakers.get(s.speaker or "", s.speaker),
                    "text": s.text,
                }
                for s in transcript.segments
            ],
            "text": transcript.text,
        },
        sources=(Source(type="meeting", id=str(transcript.meeting_id), label=transcript.title),),
    )


async def _minutes(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    detail = await MeetingService(ctx).get(_meeting_id(args))
    names: dict[str, str] = {}
    for p in detail.participants:
        if p.user_id is not None:
            names[f"u:{p.user_id}"] = p.name
        if p.contact_id is not None:
            names[f"c:{p.contact_id}"] = p.name
    minutes = detail.minutes
    grouped: list[dict[str, Any]] = []
    if minutes is not None:
        for side, groups in group_action_items(minutes.action_items):
            for owner_key, items in groups:
                owner = names.get(owner_key or "") or (
                    items[0].owner_label if items and items[0].owner_label else None
                )
                grouped.append(
                    {
                        "side": side,
                        "owner": owner,
                        "items": [
                            {
                                "title": i.title,
                                "description": i.description,
                                "due_date": i.due_date.isoformat() if i.due_date else None,
                                "at": i.at,
                                "quote": i.quote,
                                "verified": i.verified,
                                "becomes_task": i.create_task,
                            }
                            for i in items
                        ],
                    }
                )
    return ToolResult(
        data={
            "meeting_id": str(detail.id),
            "title": detail.title,
            "status": detail.status.value,
            "occurred_at": detail.occurred_at.isoformat(),
            "company": detail.company_name,
            "project": detail.project_name,
            "participants": [
                {
                    "name": p.name,
                    "side": "staff" if p.user_id else ("client" if p.contact_id else "other"),
                }
                for p in detail.participants
            ],
            "minutes": None
            if minutes is None
            else {
                "confirmed": detail.status.value == "done",
                "title": minutes.title or detail.title,
                "summary": minutes.summary,
                "topics": [{"heading": t.heading, "text": t.text} for t in minutes.topics],
                "decisions": [
                    {"text": d.text, "at": d.at, "quote": d.quote, "verified": d.verified}
                    for d in minutes.decisions
                ],
                "action_items": grouped,
                "open_questions": list(minutes.open_questions),
            },
            "interaction_id": str(detail.interaction_id) if detail.interaction_id else None,
            "task_ids": [str(t) for t in detail.task_ids],
        },
        sources=(Source(type="meeting", id=str(detail.id), label=detail.title),),
    )


MEETING_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="meetings.find",
        description=(
            "Find recorded meetings: by a search over their titles and transcripts, by client "
            "(company_id) and/or by status (review, done, failed). Newest first, at most 10. "
            "Use it to resolve 'the meeting with Nova last week' to a meeting id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Words to search titles and transcripts for.",
                },
                "company_id": {"type": "string", "description": "Narrow to one client's meetings."},
                "status": {
                    "type": "string",
                    "description": "Comma-separated statuses: review, done, failed, queued.",
                },
            },
        },
        handler=_find,
        permission=_READ,
        tags=("meetings", "read"),
    ),
    AIToolSpec(
        name="meetings.transcript",
        description=(
            "The full transcript of one meeting: every line with the seconds it was said at "
            "and the speaker's name where the roster names one, plus the flat text. Read this "
            "before answering what was said in a meeting."
        ),
        input_schema={
            "type": "object",
            "properties": {"meeting_id": {"type": "string"}},
            "required": ["meeting_id"],
        },
        handler=_transcript,
        permission=_READ,
        tags=("meetings", "read"),
    ),
    AIToolSpec(
        name="meetings.minutes",
        description=(
            "The minutes of one meeting: summary, topics, decisions, action items grouped by "
            "side (agency / client / others) and owner, open questions — each decision and "
            "action item with the transcript words it rests on and whether that quote was "
            "verified. Says whether the minutes are confirmed or still a draft."
        ),
        input_schema={
            "type": "object",
            "properties": {"meeting_id": {"type": "string"}},
            "required": ["meeting_id"],
        },
        handler=_minutes,
        permission=_READ,
        tags=("meetings", "read"),
    ),
]

__all__ = ["MEETING_MCP_TOOLS"]
