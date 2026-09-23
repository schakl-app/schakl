"""One action item of the minutes into one draft task — schakl fills the form, a person confirms.

The e-mail approve's shape (#327: "laat schakl deze taak invullen") on the review desk: the
reviewer presses *Taak aanmaken* beside an action item and gets the task's whole form filled in —
title, what exactly is to be done, the steps the meeting enumerated, the deadline that was
spoken, who took it on — reads it beside the words it came from, corrects it, and only then is a
task written (``MeetingService.create_task_for_item``).

Three things decide the posture, and two of them are inherited.

**The vocabulary is the task form** (#382's argument, not #327's): the words are the agency's
own meeting, a colleague holding ``tasks.task.create`` is watching, and nothing is written until
they press the button beside every field. So the draft may carry the whole form, and the
narrow e-mail vocabulary would only cost the reviewer retyping the half it refused.

**Grounding does not relax.** The model reads the tenant's shortlist (``candidates.gather`` with
``TASK_BLOCKS``) and an id it was never shown is dropped, per type — a colleague id in
``project_id`` is a real id from the wrong space and fails the write anyway, a project id in
``assignee_user_id`` would be a real user (``core/ai/taskdraft.draft_from_call`` is reused for
exactly this). The client and project are the *meeting's* and are pinned; the model may not
move a meeting's task to another client.

**The deadline is what was said, and it is bounded.** The minutes already read a due date off
the transcript where a date or weekday was spoken; the item's date is offered as the default,
and the model is asked once more against the words *around* the item (``_excerpt``) — "voor
het eind van de maand", "over twee weken" — resolved against the org's calendar and inside
the same window every model-read date gets. A deadline nobody spoke stays empty, and the form
asks for it (#392).
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from typing import Any

from app.core.ai import prompts
from app.core.ai.candidates import TASK_BLOCKS
from app.core.ai.candidates import gather as gather_candidates
from app.core.ai.prompts import calendar_line
from app.core.ai.providers import ChatMessage
from app.core.ai.schemas import TaskParseResult
from app.core.ai.service import AIService
from app.core.ai.taskdraft import SUBMIT_TASK, draft_from_call
from app.core.timezone import org_today, org_zoneinfo
from app.modules.meetings.minutes import FEATURE, participants_block
from app.modules.meetings.models import Meeting
from app.modules.meetings.schemas import MinutesActionItem

logger = logging.getLogger("schakl.meetings")

#: The transcript the model reads for one item: the lines around where it was said. The whole
#: recording is the wrong document for a task — it is two hours of context for one sentence —
#: and the window is what makes "the steps they listed" and "the deadline they named" findable.
_EXCERPT_BEFORE = 120.0
_EXCERPT_AFTER = 240.0
#: An item with no timestamp gets the transcript cut to this many characters instead.
_EXCERPT_CHARS = 12_000
_MAX_ROUNDS = 2


def _excerpt(segments: list[dict[str, Any]], text: str | None, at: float | None) -> str:
    """The words around the item, as ``[m:ss] Speaker: text`` lines."""
    if segments and at is not None:
        lines = [
            s
            for s in segments
            if isinstance(s, dict)
            and float(s.get("end") or 0) >= at - _EXCERPT_BEFORE
            and float(s.get("start") or 0) <= at + _EXCERPT_AFTER
        ]
    else:
        lines = [s for s in segments if isinstance(s, dict)]
    if not lines:
        return (text or "")[:_EXCERPT_CHARS]
    out: list[str] = []
    used = 0
    for s in lines:
        start = float(s.get("start") or 0)
        clock = f"{int(start // 60)}:{int(start % 60):02d}"
        who = f"{s.get('speaker')}: " if s.get("speaker") else ""
        line = f"[{clock}] {who}{s.get('text') or ''}"
        used += len(line)
        if used > _EXCERPT_CHARS:
            break
        out.append(line)
    return "\n".join(out)


def system_prompt(
    *,
    today: date,
    now: datetime | None,
    locale: str,
    agency: str,
    candidates: str,
    participants: str,
    meeting_title: str,
    company_name: str | None,
) -> str:
    parts = [
        f"You turn one action item from the minutes of a meeting at {agency}, an agency, into "
        "a draft task for the agency's own staff. You never create anything — you fill in a "
        "form a colleague who was in the meeting reads, corrects and confirms.",
        calendar_line(today, now),
        f"Write every sentence you produce in {prompts.language_name(locale)}, whatever "
        "language was spoken.",
        "You are given the action item as the minutes recorded it (its title, the words it "
        "rests on, who took it on, and a deadline if one was minuted) and the transcript "
        "around the moment it was said. The transcript comes from a speech recogniser: no "
        "punctuation to trust, names guessed, speaker labels (S1, S2 …) instead of people "
        "except where PARTICIPANTS pairs a label with a person.",
        "Rules:\n"
        "- title: the task, in a handful of words. Keep the minutes' title unless the words "
        "say it better.\n"
        "- description: what exactly is to be done and anything a colleague picking it up needs "
        "that is not the title or a step — a constraint, a preference the client stated, a "
        "reason. Markdown; short. Never restate the transcript, never say something was not "
        "discussed.\n"
        "- checklist_items: the separable steps the meeting enumerated for this item, in order. "
        "None when it enumerated none — one vague step is worse than no checklist.\n"
        "- due_date: only when a date, a weekday or a period ('eind van de maand', 'over twee "
        "weken') was actually spoken for this item, resolved against the calendar above. The "
        "minutes' own date, when given, is the default; move it only if the words say another. "
        "Never invent one.\n"
        "- assignee_user_id: the colleague who took it on, from COLLEAGUES (or a PARTICIPANTS "
        "row of kind staff), by id copied character for character. Null when it is a client's "
        "contact or somebody outside the list.\n"
        "- allocated_minutes: only when an amount of work was spoken.\n"
        "- links: only a URL that was actually spelled out.\n"
        "- Leave company_id and project_id null: the task is the meeting's client's.",
        f"The meeting: “{meeting_title}”" + (f", with {company_name}." if company_name else "."),
    ]
    if participants:
        parts.append(
            "PARTICIPANTS (label\tname\tkind\tid) — who was in the meeting, which speaker label "
            f"they are, their side, and their id:\n{participants}"
        )
    if candidates:
        parts.append(
            "These are the tenant's own records. Copy an id character for character — never "
            f"edit, shorten or invent one.\n\n{candidates}"
        )
    parts.append(prompts._INJECTION_STANCE)  # noqa: SLF001 — the shared stance, on purpose
    return "\n\n".join(parts)


def item_document(
    item: MinutesActionItem, *, excerpt: str, owner_name: str | None
) -> str:
    """The item and its context as data inside JSON — never as instructions."""
    document = {
        "action_item": {
            "title": item.title,
            "description": item.description,
            "quote": item.quote,
            "said_at_seconds": item.at,
            "owner": owner_name,
            "owner_label": item.owner_label,
            "due_date": item.due_date.isoformat() if item.due_date else None,
        },
        "transcript_around_it": excerpt,
    }
    return json.dumps(document, ensure_ascii=False)


async def draft_task_for_item(
    service: AIService,
    *,
    row: Meeting,
    item: MinutesActionItem,
    participants: list[Any],
    agency: str,
    locale: str,
    owner_name: str | None,
    company_name: str | None = None,
    override_budget: bool = False,
) -> TaskParseResult:
    """One action item into one draft task (the dictation's machinery, one record over).

    Raises ``AppError`` on a provider failure; the route turns it into the envelope.
    """
    ctx = service.ctx
    ctx.require("tasks.task.create")
    today = await org_today(ctx.session, ctx.org.id)
    now = datetime.now(await org_zoneinfo(ctx.session, ctx.org.id))
    candidates = await gather_candidates(ctx, item.title, blocks=TASK_BLOCKS)
    roster, _contact_ids = participants_block(participants)
    system = system_prompt(
        today=today,
        now=now,
        locale=locale,
        agency=agency,
        candidates=candidates.as_prompt_block(),
        participants=roster,
        meeting_title=row.title,
        company_name=company_name,
    )
    transcript = dict(row.transcript or {})
    segments = [s for s in (transcript.get("segments") or []) if isinstance(s, dict)]
    document = item_document(
        item,
        excerpt=_excerpt(segments, row.transcript_text, item.at),
        owner_name=owner_name,
    )
    submitted: dict[str, Any] = {}
    truncated = False
    history: list[ChatMessage] = [ChatMessage(role="user", content=document)]
    try:
        for round_no in range(_MAX_ROUNDS):
            force = SUBMIT_TASK.name if round_no == _MAX_ROUNDS - 1 else None
            text, calls = await service.complete(
                FEATURE,
                system=system,
                messages=history,
                tools=[SUBMIT_TASK],
                force_tool=force,
                override_budget=override_budget,
            )
            call = next((c for c in calls if c.name == SUBMIT_TASK.name), None)
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
        candidates=candidates,
        statuses=candidates.status_keys,
        today=today,
        pinned_company=row.company_id,
        pinned_project=row.project_id,
    )
    # The meeting's client and project are the task's, whatever the model answered: a task
    # made from a meeting with Nova is Nova's.
    draft.company_id = row.company_id
    draft.project_id = row.project_id if draft.project_id is None else draft.project_id
    if draft.title is None:
        draft.title = item.title
    if draft.due_date is None:
        draft.due_date = item.due_date
    if draft.assignee_user_id is None and item.owner_contact_id is None:
        draft.assignee_user_id = item.assignee_user_id
    draft.truncated = truncated
    return draft


__all__ = ["draft_task_for_item", "item_document", "system_prompt"]
