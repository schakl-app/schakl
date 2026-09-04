"""Speak a task (#382) — transcription, and one dictation into one draft task.

The machinery is #246's, one record over: the browser records, the tenant's own speech provider
transcribes, and the words come back for the speaker to read before anything is parsed. What is
new is the *second* step — turning those words into a whole task rather than a title — and the
one design decision behind it.

**The vocabulary is the task form, and that follows from who spoke and who is watching.**
#327 (email → task) deliberately narrowed what a model may write to six fields: no assignee, no
client, no status, and above all no ``visible_to_client``. That was right *there* for two
reasons which are both inverted here. Its input is written by someone outside the organisation;
this input is a colleague speaking into their own microphone, on a session holding
``tasks.task.create``. And its plan is applied by an ARQ worker with nobody in front of a
screen; this one prefills a form, creates nothing, and is written only when a person presses a
button beside every field it filled in. Copying the narrow schema here would keep the shape and
drop the reason — and the only effect would be the speaker retyping the half the schema refused
to carry.

**What does not change is grounding.** An id the model was never shown is dropped, never
guessed — ``features._checked_uuid``, against ``candidates``' evidence set. And it is checked
*per type* here (``member_ids()``, ``label_ids()``), unlike the time parse's single pool: a
project id offered as a company fails the write anyway, while a label's id in
``assignee_user_id`` is a real user id from the same space. A misheard "Jansen" must come back
as **no client selected**, which the form shows in one glance, and never as somebody else's
client, which nobody notices until the invoice.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from app.core.ai import prompts
from app.core.ai.audio import decode_clip
from app.core.ai.candidates import TASK_BLOCKS
from app.core.ai.candidates import gather as gather_candidates
from app.core.ai.providers import AIProviderError, ChatMessage, ProviderConfig, ToolDef
from app.core.ai.schemas import (
    TaskDraftChecklistItem,
    TaskDraftLink,
    TaskParseRequest,
    TaskParseResult,
    TaskTranscribeRequest,
    TimeTranscribeResult,
)
from app.core.ai.service import AIService
from app.core.ai.transcribe import transcribe as provider_transcribe
from app.core.timezone import org_today
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.modules.tasks.models import TaskPriority
from app.modules.tasks.system import MAX_CHECKLIST_ITEMS, MAX_LINKS

logger = logging.getLogger(__name__)

#: The AI-core feature key. Its own rather than ``time_assist``'s — see ``AI_FEATURES``.
FEATURE = "task_assist"

#: One free round, then a forced submit. Same shape as the time parse: the candidate shortlist
#: makes discovery unnecessary, so the usual dictation is a single provider call.
_MAX_ROUNDS = 2

#: A deadline heard in a sentence is a guess about a calendar, so it is bounded (#327's window).
#: Backwards by a few days rather than not at all, because "maandag" spoken on a Tuesday is a
#: real thing people say about a deadline that has just passed.
_DUE_PAST_DAYS = 7
_DUE_FUTURE_DAYS = 730

#: A day of work is the largest estimate a dictated task plausibly carries; anything above it is
#: a misheard number, and ``TaskCreate`` would take it silently.
_MAX_ALLOCATED_MINUTES = 100_000

_MAX_LABELS = 10

SUBMIT_TASK = ToolDef(
    name="submit_task",
    description="Submit the drafted task. Call exactly once, as your final act.",
    input_schema={
        "type": "object",
        "properties": {
            "title": {
                "type": ["string", "null"],
                "description": "What the task is, in a handful of words. Never the sentence.",
            },
            "description": {
                "type": ["string", "null"],
                "description": (
                    "Only what a colleague picking this up needs and cannot read off the "
                    "title or the steps. Null is the common answer."
                ),
            },
            "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "priority": {
                "type": ["string", "null"],
                "enum": [*(p.value for p in TaskPriority), None],
            },
            "status": {
                "type": ["string", "null"],
                "description": "One of the org's status keys, verbatim, or null.",
            },
            "company_id": {"type": ["string", "null"]},
            "project_id": {"type": ["string", "null"]},
            "assignee_user_id": {
                "type": ["string", "null"],
                "description": "The colleague the speaker named, from the list. Null otherwise.",
            },
            "label_ids": {
                "type": "array",
                "maxItems": _MAX_LABELS,
                "items": {"type": "string"},
            },
            "allocated_minutes": {
                "type": ["integer", "null"],
                "description": "An estimate of the work, in minutes. Not a deadline.",
            },
            "checklist_title": {"type": ["string", "null"]},
            "checklist_items": {
                "type": "array",
                "maxItems": MAX_CHECKLIST_ITEMS,
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "description": {"type": ["string", "null"]},
                    },
                    "required": ["title"],
                    "additionalProperties": False,
                },
                "description": (
                    "The steps the speaker enumerated, in order. Omit entirely when the "
                    "dictation enumerates none."
                ),
            },
            "links": {
                "type": "array",
                "maxItems": MAX_LINKS,
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": ["string", "null"]},
                    },
                    "required": ["url"],
                    "additionalProperties": False,
                },
                "description": "Only a URL the speaker actually spelled out.",
            },
            "requires_interaction": {"type": ["boolean", "null"]},
            "visible_to_client": {"type": ["boolean", "null"]},
        },
        "required": [],
        "additionalProperties": False,
    },
)


async def transcribe_task(
    service: AIService, payload: TaskTranscribeRequest
) -> TimeTranscribeResult:
    """Turn a recorded clip into text for the dictation field (#382).

    The route declares ``ai.use``, which is what makes the surface enumerable (§15); the
    row-shaped half of the rule lives here, exactly as #246 put it: the transcript exists to
    become a task, so the caller must be able to create one. AI access alone must not reach a
    microphone that bills the tenant's audio budget.

    Nothing is stored. The words go straight back for the speaker to read and correct.
    """
    ctx = service.ctx
    ctx.require("tasks.task.create")
    clip = decode_clip(payload.audio)
    config = await service.speech_config(FEATURE)
    await service.ensure_audio_budget(override=payload.override_budget)
    language = (payload.language or service.locale() or "").split("-")[0] or None
    try:
        async with ctx.release_db():
            result = await provider_transcribe(config, clip, language=language)
    except AIProviderError as exc:
        logger.warning("task dictation transcription failed (%s): %s", config.provider, exc)
        raise AppError(
            "ai_provider_error", "errors.ai_provider_error", status_code=502
        ) from exc
    # Seconds, never folded into the token counters (#246): an audio model reports no tokens.
    await service.record_usage(FEATURE, config.model, 0, 0, audio_seconds=result.seconds)
    return TimeTranscribeResult(text=result.text)


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:limit] or None


def _uuid_in(value: Any, allowed: set[str]) -> uuid.UUID | None:
    """The #129 rule: an id the model was never shown is dropped, never guessed."""
    if not isinstance(value, str) or value.strip().lower() not in allowed:
        return None
    try:
        return uuid.UUID(value.strip())
    except ValueError:
        return None


def _minutes(value: Any) -> int | None:
    """An estimate as any number the model might send — ``2.5``, ``"150"``, ``150``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        try:
            value = float(value.strip().replace(",", "."))
        except ValueError:
            return None
    if not isinstance(value, int | float):
        return None
    minutes = int(round(value))
    return minutes if 0 < minutes <= _MAX_ALLOCATED_MINUTES else None


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


def _safe_url(value: Any) -> str | None:
    """A link the speaker dictated, or ``None`` to drop it.

    Dropped rather than raised, ``tasks.system._safe_url``'s reasoning: a 422 over one bad row
    in a list nobody typed would throw the whole dictation away, and the form the speaker is
    about to see is where a missing link is noticed.
    """
    url = _text(value, 1024)
    if url is None:
        return None
    if "://" not in url:
        url = f"https://{url}"
    try:
        reject_dangerous_url(url, field="url")
    except AppError:
        return None
    return url


def draft_from_call(
    submitted: dict[str, Any],
    *,
    candidates,  # noqa: ANN001 - ParseCandidates, kept import-light
    statuses: set[str],
    today: date,
    pinned_company: uuid.UUID | None = None,
    pinned_project: uuid.UUID | None = None,
) -> TaskParseResult:
    """Turn the model's one tool call into the draft the form is filled from.

    Every field is re-derived rather than passed through: the schema tells the model what shape
    to answer in and guarantees nothing whatsoever about what arrives.
    """
    seen = candidates.ids()
    priority = submitted.get("priority")
    priority = (
        priority if isinstance(priority, str) and priority in {p.value for p in TaskPriority}
        else None
    )

    items: list[TaskDraftChecklistItem] = []
    raw_items = submitted.get("checklist_items")
    if isinstance(raw_items, list):
        for entry in raw_items[:MAX_CHECKLIST_ITEMS]:
            if not isinstance(entry, dict):
                continue
            title = _text(entry.get("title"), 512)
            if title:
                items.append(
                    TaskDraftChecklistItem(
                        title=title, description=_text(entry.get("description"), 2000)
                    )
                )

    links: list[TaskDraftLink] = []
    raw_links = submitted.get("links")
    if isinstance(raw_links, list):
        for entry in raw_links[:MAX_LINKS]:
            if not isinstance(entry, dict):
                continue
            url = _safe_url(entry.get("url"))
            if url:
                links.append(TaskDraftLink(url=url, title=_text(entry.get("title"), 255)))

    label_ids: list[uuid.UUID] = []
    raw_labels = submitted.get("label_ids")
    if isinstance(raw_labels, list):
        allowed = candidates.label_ids()
        for entry in raw_labels[:_MAX_LABELS]:
            found = _uuid_in(entry, allowed)
            if found is not None and found not in label_ids:
                label_ids.append(found)

    status = submitted.get("status")
    requires = submitted.get("requires_interaction")
    visible = submitted.get("visible_to_client")
    return TaskParseResult(
        title=_text(submitted.get("title"), 512),
        description=_text(submitted.get("description"), 20_000),
        due_date=_due(submitted.get("due_date"), today=today),
        priority=priority,
        # A slug, not a UUID, so membership in the org's own vocabulary is its grounding
        # (``features._checked_key``'s rule). An unknown key is dropped rather than passed on:
        # the create would 422 it, and a 422 on a *draft* helps nobody.
        status=status.strip().lower()
        if isinstance(status, str) and status.strip().lower() in statuses
        else None,
        # The pin is a default, never a filter: a spoken client wins over the screen's, because
        # a draft that silently disagrees with the words it was made from is the worse failure.
        company_id=_uuid_in(submitted.get("company_id"), seen) or pinned_company,
        project_id=_uuid_in(submitted.get("project_id"), seen) or pinned_project,
        assignee_user_id=_uuid_in(submitted.get("assignee_user_id"), candidates.member_ids()),
        label_ids=label_ids,
        allocated_minutes=_minutes(submitted.get("allocated_minutes")),
        checklist_title=_text(submitted.get("checklist_title"), 255),
        checklist_items=items,
        links=links,
        # Tri-state, both of them. `False` and "the speaker said nothing" are different facts
        # and the form needs to tell them apart to keep its own defaults (#284's rule).
        requires_interaction=requires if isinstance(requires, bool) else None,
        visible_to_client=visible if isinstance(visible, bool) else None,
    )


async def parse_task(
    service: AIService, payload: TaskParseRequest, *, config: ProviderConfig | None = None
) -> TaskParseResult:
    """One dictation into one draft task (#382).

    The tenant's own records are resolved *before* the model runs (``candidates.gather`` with
    ``TASK_BLOCKS``) and handed over as a shortlist, so the usual dictation costs one provider
    call rather than three serial find round trips.

    ``max_tokens`` is deliberately absent — the provider default. docs/AI.md's #371 lesson:
    this schema invites twenty checklist items, a description and ten links, a cap sized for
    "a handful of short fields" truncates that silently (a tool call's arguments stream as one
    JSON string), and the result reads as *"schakl could not make a task of this"* over a
    perfectly good dictation. A cap costs nothing when the answer is short.
    """
    ctx = service.ctx
    ctx.require("tasks.task.create")
    today = payload.today or await org_today(ctx.session, ctx.org.id)
    candidates = await gather_candidates(ctx, payload.text, blocks=TASK_BLOCKS)

    pinned = ""
    if payload.company_id or payload.project_id:
        pinned = (
            "The speaker is working on a screen that already names a client and/or project; "
            "the form will use it. Only set company_id or project_id when the dictation names "
            "a different one."
        )
    system = prompts.task_parse_system(
        today=today,
        locale=service.locale(),
        candidates=candidates.as_prompt_block(),
        pinned=pinned,
    )

    submitted: dict[str, Any] = {}
    truncated = False
    history: list[ChatMessage] = [ChatMessage(role="user", content=payload.text)]
    try:
        for round_no in range(_MAX_ROUNDS):
            force = SUBMIT_TASK.name if round_no == _MAX_ROUNDS - 1 else None
            text, calls = await service.complete(
                FEATURE,
                system=system,
                messages=history,
                tools=[SUBMIT_TASK],
                force_tool=force,
                override_budget=payload.override_budget,
                config=config,
            )
            call = next((c for c in calls if c.name == SUBMIT_TASK.name), None)
            if call is not None:
                # A cut-off answer is not an empty one (docs/AI.md). Unlike #327 this does not
                # raise: the speaker is waiting, and a partial draft over words they can still
                # see beats an error that throws the dictation away. It is *reported* instead,
                # so the form can say the plan may be short rather than presenting it whole.
                truncated = call.incomplete or service.truncated
                submitted = call.input
                break
            if not calls:
                break
            history.append(ChatMessage(role="assistant", content=text, tool_calls=tuple(calls)))
    finally:
        # Tokens spent by a run that failed halfway are still spent (docs/AI.md).
        await service.flush_usage(FEATURE)

    draft = draft_from_call(
        submitted,
        candidates=candidates,
        statuses=candidates.status_keys,
        today=today,
        pinned_company=payload.company_id,
        pinned_project=payload.project_id,
    )
    draft.truncated = truncated
    return draft


__all__ = ["FEATURE", "SUBMIT_TASK", "draft_from_call", "parse_task", "transcribe_task"]
