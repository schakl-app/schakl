"""Feature services on top of the AI core: writing assist (#128), time assist (#129),
client digest and report drafts (#130).

The rule that binds them all: **AI proposes, the user disposes** — nothing here mutates a
record. The numbers in a digest or report come from the platform's own services (panel
providers, module tools); the model writes prose, never arithmetic.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select

from app.config import settings
from app.core.activity.service import ActivityService, snapshot
from app.core.ai import prompts
from app.core.ai.candidates import gather as gather_candidates
from app.core.ai.models import AIReport
from app.core.ai.providers import ChatMessage, ToolDef
from app.core.ai.schemas import (
    ReportCreate,
    ReportGenerateRequest,
    ReportRead,
    ReportUpdate,
    TimeParseRequest,
    TimeParseResult,
    TimeReconstructRequest,
    TimeReconstructResult,
    TimeSuggestion,
    WritingAssistRequest,
)
from app.core.ai.service import AIService
from app.core.ai.tools import get_tool, get_tools, result_text, run_tool
from app.core.timezone import org_today
from app.errors import AppError
from app.registry import registry

_UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)

#: Reconstruction only nags when the gap is real: a quarter of an hour is rounding noise.
_SHORT_DAY_TOLERANCE_MINUTES = 15

_REPORT_TRACKED_FIELDS = ("title", "period", "language")


# --------------------------------------------------------------------------- #
# Writing assist (#128)
# --------------------------------------------------------------------------- #
async def stream_writing_assist(
    service: AIService, payload: WritingAssistRequest
) -> AsyncIterator[dict[str, Any]]:
    system = prompts.writing_system(
        action=payload.action,
        house_style=await service.house_style(),
        entity_type=payload.entity_type,
        title=payload.title,
        target_locale=payload.target_locale,
    )
    async for event in service.stream(
        "writing_assist",
        system=system,
        messages=[ChatMessage(role="user", content=payload.text)],
        override_budget=payload.override_budget,
    ):
        if event.kind == "text":
            yield {"event": "text", "data": {"text": event.text}}
    yield {"event": "done", "data": {}}


# --------------------------------------------------------------------------- #
# Time assist (#129) — natural-language quick add
# --------------------------------------------------------------------------- #
_SUBMIT_ENTRY = ToolDef(
    name="submit_time_entry",
    description="Submit the parsed draft time entry. Call exactly once, as your final act.",
    input_schema={
        "type": "object",
        "properties": {
            "date": {"type": ["string", "null"], "description": "YYYY-MM-DD"},
            "start": {"type": ["string", "null"], "description": "HH:MM, 24-hour"},
            "end": {"type": ["string", "null"], "description": "HH:MM, 24-hour"},
            "duration_minutes": {"type": ["integer", "null"]},
            "company_id": {"type": ["string", "null"]},
            "project_id": {"type": ["string", "null"]},
            "task_id": {"type": ["string", "null"]},
            "description": {"type": ["string", "null"]},
            "entry_type_key": {
                "type": ["string", "null"],
                "description": (
                    "One of the org's active entry-type keys, copied verbatim from the list "
                    "you were given. Null when the text names no type."
                ),
            },
            "billable": {
                "type": ["boolean", "null"],
                "description": (
                    "Only when the text says so ('niet declarabel', 'non-billable', "
                    "'declarabel'). Null otherwise — the project decides the default."
                ),
            },
            "break_minutes": {
                "type": ["integer", "null"],
                "description": "Unpaid break inside the span, in minutes ('half uur pauze').",
            },
        },
        "required": [],
        "additionalProperties": False,
    },
)

_PARSE_TOOL_NAMES = ("companies.find", "projects.find", "tasks.find")
#: One free round then a forced submit (#246). The candidate shortlist (``candidates.gather``)
#: makes the find tools a fallback rather than the main path, so the typical parse is a single
#: provider call instead of the three-to-four serial ones the discovery loop needed.
_PARSE_MAX_ROUNDS = 2
#: A break longer than a day is a misread, not a break.
_MAX_BREAK_MINUTES = 24 * 60
#: A draft entry is a dozen short fields. The 8192 default is sized for a written report and
#: only costs latency here.
_PARSE_MAX_TOKENS = 1024


def _seen_ids(texts: list[str]) -> set[str]:
    """Every UUID a tool result actually contained — the only IDs a parse may use."""
    seen: set[str] = set()
    for text in texts:
        seen.update(m.lower() for m in _UUID_RE.findall(text))
    return seen


def _checked_uuid(value: Any, seen: set[str]) -> uuid.UUID | None:
    if not isinstance(value, str) or value.lower() not in seen:
        return None
    try:
        return uuid.UUID(value)
    except ValueError:
        return None


def _checked_key(value: Any, allowed: set[str]) -> str | None:
    """An entry type is a tenant-defined slug, not a UUID, so ``_seen_ids`` cannot vouch for
    it — membership in the org's own active keys is its grounding. An unknown key is dropped
    rather than passed on: the write path would 422 it, and a 422 on a *draft* helps nobody."""
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    return key if key in allowed else None


def _parse_hhmm(value: Any) -> str | None:
    """A clock time, however the model chose to write it.

    Models emit ``14:00`` most of the time and ``14.00``/``1430``/``9:00`` the rest of it. All
    three mean the same thing to a human, so all three are accepted — the previous ``H:MM``-only
    regex dropped the others to ``None``, which reaches the user as "the AI ignored my times".
    """
    if isinstance(value, int) and 0 <= value <= 2359:
        value = f"{value // 100}:{value % 100:02d}"
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = re.fullmatch(r"(\d{1,2})[:.h](\d{2})", text, re.IGNORECASE) or re.fullmatch(
        r"(\d{1,2})(\d{2})", text
    )
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if hours > 23 or minutes > 59:
        return None
    return f"{hours:02d}:{minutes:02d}"


def _parse_minutes(value: Any) -> int | None:
    """Minutes as any number the model might send. ``2.5`` and ``"150"`` are as meaningful as
    ``150``; the old ``isinstance(value, int)`` check silently discarded both."""
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
    return minutes if minutes > 0 else None


async def parse_time_entry(service: AIService, payload: TimeParseRequest) -> TimeParseResult:
    """Server-side parse of one quick-add line into a draft entry (#129, #246).

    The tenant's own records are resolved **before** the model runs (``candidates.gather``) and
    handed over as a shortlist, so the usual parse is one provider call rather than the three
    serial ``companies.find`` → ``projects.find`` → ``tasks.find`` round trips discovery needed.
    The find tools stay on the request for whatever the shortlist missed.

    Grounding is unchanged in kind and only wider in evidence: an id the model was never shown
    is dropped, never guessed. The shortlist adds to what counts as "shown"; it does not relax
    the check.
    """
    ctx = service.ctx
    # Read the calendar and the tenant's records while we still hold the connection — both are
    # needed to build the prompt, and `complete()` hands the connection back for the call.
    today = payload.today or await org_today(ctx.session, ctx.org.id)
    candidates = await gather_candidates(ctx, payload.text)

    specs = get_tools(ctx, _PARSE_TOOL_NAMES)
    defs = [ToolDef(s.name, s.description, s.input_schema) for s in specs] + [_SUBMIT_ENTRY]
    by_name = {s.name: s for s in specs}

    system = prompts.time_parse_system(
        today=today,
        locale=service.locale(),
        candidates=candidates.as_prompt_block(),
        has_tools=bool(specs),
    )
    history: list[ChatMessage] = [ChatMessage(role="user", content=payload.text)]
    tool_texts: list[str] = []
    submitted: dict[str, Any] = {}

    try:
        for round_no in range(_PARSE_MAX_ROUNDS):
            force = "submit_time_entry" if round_no == _PARSE_MAX_ROUNDS - 1 else None
            text, calls = await service.complete(
                "time_assist",
                system=system,
                messages=history,
                tools=defs,
                force_tool=force,
                override_budget=payload.override_budget,
                max_tokens=_PARSE_MAX_TOKENS,
            )
            if not calls:
                break
            history.append(ChatMessage(role="assistant", content=text, tool_calls=tuple(calls)))
            done = False
            for call in calls:
                if call.name == "submit_time_entry":
                    submitted = call.input
                    done = True
                    break
                spec = by_name.get(call.name)
                result = (
                    result_text(await run_tool(ctx, spec, call.input))
                    if spec is not None
                    else '{"error": "unknown tool"}'
                )
                tool_texts.append(result)
                history.append(ChatMessage(role="tool", content=result, tool_call_id=call.id))
            if done:
                break
    finally:
        # Tokens spent by a loop that failed halfway are still spent.
        await service.flush_usage("time_assist")

    # The shortlist is evidence exactly as a tool result is. Forgetting this union is the one
    # change that would silently return an all-null draft for a perfectly parsed line.
    seen = _seen_ids(tool_texts) | candidates.ids()
    parsed_date: date | None = None
    if isinstance(submitted.get("date"), str):
        try:
            parsed_date = date.fromisoformat(submitted["date"])
        except ValueError:
            parsed_date = None
    billable = submitted.get("billable")
    break_minutes = _parse_minutes(submitted.get("break_minutes"))
    return TimeParseResult(
        date=parsed_date,
        start=_parse_hhmm(submitted.get("start")),
        end=_parse_hhmm(submitted.get("end")),
        duration_minutes=_parse_minutes(submitted.get("duration_minutes")),
        company_id=_checked_uuid(submitted.get("company_id"), seen),
        project_id=_checked_uuid(submitted.get("project_id"), seen),
        task_id=_checked_uuid(submitted.get("task_id"), seen),
        description=(submitted.get("description") or None)
        if isinstance(submitted.get("description"), str | type(None))
        else None,
        entry_type_key=_checked_key(
            submitted.get("entry_type_key"), candidates.entry_type_keys
        ),
        # Tri-state on purpose: `None` means "the text said nothing", which is what lets the
        # form keep the project's own billable default (#284). A `False` here would look like
        # a decision the user never made.
        billable=billable if isinstance(billable, bool) else None,
        break_minutes=break_minutes
        if break_minutes is not None and break_minutes <= _MAX_BREAK_MINUTES
        else None,
    )


# --------------------------------------------------------------------------- #
# Time assist (#129) — day reconstruction
# --------------------------------------------------------------------------- #
_SUBMIT_SUGGESTIONS = ToolDef(
    name="submit_suggestions",
    description="Submit the drafted suggestions. Call exactly once.",
    input_schema={
        "type": "object",
        "properties": {
            "suggestions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "company_id": {"type": ["string", "null"]},
                        "project_id": {"type": ["string", "null"]},
                        "task_id": {"type": ["string", "null"]},
                        "minutes": {"type": ["integer", "null"]},
                        "description": {"type": "string"},
                        "label": {
                            "type": "string",
                            "description": "Short chip label naming the work",
                        },
                    },
                    "required": ["description", "label"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["suggestions"],
        "additionalProperties": False,
    },
)


async def _tool_data(service: AIService, name: str, args: dict[str, Any]) -> Any:
    """Run a registry tool by name under the caller's context; None when the module that
    contributes it is disabled or the caller may not use it — signals stay additive."""
    spec = get_tool(service.ctx, name)
    if spec is None:
        return None
    result = await run_tool(service.ctx, spec, args)
    if isinstance(result.data, dict) and "error" in result.data:
        return None
    return result.data


async def reconstruct_day(
    service: AIService, payload: TimeReconstructRequest
) -> TimeReconstructResult:
    """Suggest draft entries for a short day, from signals the user already owns: their
    task activity, tasks assigned to them that moved, minus approved leave (§14 — leave is
    never double-entered). "Short" is measured against the work schedule, never a
    hardcoded day length."""
    day = payload.date.isoformat()
    schedule = await _tool_data(service, "leave.day_schedule", {"date": day}) or {}
    logged = await _tool_data(
        service, "time.summary", {"date_from": day, "date_to": day}
    ) or {}

    scheduled_minutes = int(schedule.get("scheduled_minutes") or 0)
    leave_minutes = int(schedule.get("leave_minutes") or 0)
    logged_minutes = int(logged.get("minutes") or 0)
    missing = scheduled_minutes - leave_minutes - logged_minutes
    short = scheduled_minutes > 0 and missing > _SHORT_DAY_TOLERANCE_MINUTES
    result = TimeReconstructResult(
        short=short,
        scheduled_minutes=scheduled_minutes,
        logged_minutes=logged_minutes,
        leave_minutes=leave_minutes,
    )
    if not short:
        return result

    activity = await _tool_data(service, "tasks.my_activity", {"date": day})
    if not activity:
        return result
    signals = {
        "date": day,
        "missing_minutes": missing,
        "already_logged": logged.get("entries") or [],
        "task_activity": activity,
    }
    _, calls = await service.complete(
        "time_assist",
        system=prompts.time_reconstruct_system(
            today=await org_today(service.ctx.session, service.ctx.org.id), target=payload.date
        ),
        messages=[
            ChatMessage(
                role="user", content=json.dumps(signals, ensure_ascii=False, default=str)
            )
        ],
        tools=[_SUBMIT_SUGGESTIONS],
        force_tool="submit_suggestions",
        override_budget=payload.override_budget,
    )
    await service.flush_usage("time_assist")
    raw = calls[0].input.get("suggestions") if calls else None
    if not isinstance(raw, list):
        return result

    seen = _seen_ids([json.dumps(signals, default=str)])
    remaining = missing
    for item in raw[:6]:
        if not isinstance(item, dict) or remaining <= 0:
            continue
        minutes = item.get("minutes")
        minutes = int(minutes) if isinstance(minutes, int) and minutes > 0 else None
        if minutes is not None:
            minutes = min(minutes, remaining)
            remaining -= minutes
        suggestion = TimeSuggestion(
            company_id=_checked_uuid(item.get("company_id"), seen),
            project_id=_checked_uuid(item.get("project_id"), seen),
            task_id=_checked_uuid(item.get("task_id"), seen),
            minutes=minutes,
            description=str(item.get("description") or "")[:500],
            label=str(item.get("label") or "")[:120],
        )
        if suggestion.label or suggestion.description:
            result.suggestions.append(suggestion)
    return result


# --------------------------------------------------------------------------- #
# Client digest + report drafts (#130)
# --------------------------------------------------------------------------- #
async def gather_company_facts(
    service: AIService, company_id: uuid.UUID
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Assemble the structured facts for one company from the panel providers every enabled
    module already registers — the same data the company page composes (#130). Raises the
    hub module's own 404 when the company is not the caller's to see."""
    ctx = service.ctx
    ctx.require("companies.company.read")
    facts: dict[str, Any] = {}
    for spec in registry.panels_for("company", settings.enabled_modules):
        try:
            facts[spec.key] = await spec.provider(ctx, company_id)
        except AppError:
            # The hub panel not finding the company is fatal; a satellite panel the caller
            # may not read simply contributes nothing.
            if spec.key == "companies.details":
                raise
    if "companies.details" not in facts:
        raise AppError("not_found", "errors.not_found", status_code=404)
    name = str(facts["companies.details"].get("name") or "")
    sources = [
        {"type": "company", "id": str(company_id), "label": name},
        {"type": "time_report", "id": str(company_id), "label": name},
    ]
    return facts, sources


async def stream_digest(
    service: AIService,
    company_id: uuid.UUID,
    facts: dict[str, Any],
    sources: list[dict[str, str]],
    *,
    override_budget: bool,
) -> AsyncIterator[dict[str, Any]]:
    system = prompts.digest_system(
        locale=service.locale(),
        brand=service.ctx.org.name,
        today=datetime.now(UTC).date(),
    )
    async for event in service.stream(
        "reporting",
        system=system,
        messages=[
            ChatMessage(role="user", content=json.dumps(facts, ensure_ascii=False, default=str))
        ],
        override_budget=override_budget,
    ):
        if event.kind == "text":
            yield {"event": "text", "data": {"text": event.text}}
    yield {"event": "sources", "data": {"sources": sources}}
    yield {"event": "done", "data": {}}


async def stream_report(
    service: AIService, payload: ReportGenerateRequest
) -> AsyncIterator[dict[str, Any]]:
    facts, sources = await gather_company_facts(service, payload.company_id)
    month = await _tool_data(
        service,
        "time.company_month",
        {"company_id": str(payload.company_id), "period": payload.period},
    )
    if month is not None:
        facts["time.month"] = month
    system = prompts.report_system(
        language=payload.language, period=payload.period, brand=service.ctx.org.name
    )
    async for event in service.stream(
        "reporting",
        system=system,
        messages=[
            ChatMessage(role="user", content=json.dumps(facts, ensure_ascii=False, default=str))
        ],
        override_budget=payload.override_budget,
    ):
        if event.kind == "text":
            yield {"event": "text", "data": {"text": event.text}}
    yield {"event": "sources", "data": {"sources": sources}}
    yield {"event": "done", "data": {}}


class ReportsService:
    """Stored report drafts (#130): records, auditable (§16), never auto-sent."""

    def __init__(self, ctx) -> None:  # noqa: ANN001 - RequestContext, kept import-light
        self.ctx = ctx
        self.activity = ActivityService(ctx)

    async def list(self, company_id: uuid.UUID | None = None) -> list[ReportRead]:
        query = (
            select(AIReport)
            .where(AIReport.org_id == self.ctx.org.id)
            .order_by(AIReport.period.desc(), AIReport.created_at.desc())
        )
        if company_id is not None:
            query = query.where(AIReport.company_id == company_id)
        rows = (await self.ctx.session.scalars(query)).all()
        return [ReportRead.model_validate(row) for row in rows]

    async def _get(self, report_id: uuid.UUID) -> AIReport:
        row = await self.ctx.session.scalar(
            select(AIReport).where(
                AIReport.org_id == self.ctx.org.id, AIReport.id == report_id
            )
        )
        if row is None:
            raise AppError("not_found", "errors.not_found", status_code=404)
        return row

    async def get(self, report_id: uuid.UUID) -> ReportRead:
        return ReportRead.model_validate(await self._get(report_id))

    async def create(self, data: ReportCreate) -> ReportRead:
        user = self.ctx.user
        row = AIReport(
            org_id=self.ctx.org.id,
            company_id=data.company_id,
            period=data.period,
            language=data.language,
            title=data.title,
            content=data.content,
            created_by_user_id=user.id,
            created_by_name=user.full_name or user.email,
        )
        self.ctx.session.add(row)
        await self.ctx.session.flush()
        await self.ctx.session.refresh(row)
        await self.activity.record_created(AIReport.__entity_type__, row.id)
        return ReportRead.model_validate(row)

    async def update(self, report_id: uuid.UUID, data: ReportUpdate) -> ReportRead:
        row = await self._get(report_id)
        before = snapshot(row, _REPORT_TRACKED_FIELDS)
        content_changed = False
        payload = data.model_dump(exclude_unset=True)
        for key, value in payload.items():
            if key == "content":
                content_changed = value != row.content
            if value is not None:
                setattr(row, key, value)
        await self.activity.record_update(
            AIReport.__entity_type__, row.id, before, snapshot(row, _REPORT_TRACKED_FIELDS)
        )
        if content_changed:
            await self.activity.record(AIReport.__entity_type__, row.id, "content_edited")
        await self.ctx.session.flush()
        await self.ctx.session.refresh(row)
        return ReportRead.model_validate(row)

    async def delete(self, report_id: uuid.UUID) -> None:
        row = await self._get(report_id)
        await self.activity.record(
            AIReport.__entity_type__, row.id, "deleted", {"title": row.title}
        )
        await self.ctx.session.delete(row)
        await self.ctx.session.flush()
