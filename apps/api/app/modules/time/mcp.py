"""Executable AI tools the time module contributes (#127, #129, #130; CLAUDE.md §6, §12).

Every handler runs under the caller's :class:`RequestContext`, so a tool can never answer
across tenants (Golden Rule 1) or beyond the caller's role (§15). ``time.summary`` is the
"my hours" tool and is pinned to the current user regardless of scope; the company tools are
the manager surface (``time.report.read``, like ``TimeService.report``) and speak **hours
only** — rates and money stay in the report endpoints. Aggregation mirrors the module's
worked-minutes definition: ``minutes`` is already ``(ended − started) − break``, and running
timers (``ended_at IS NULL``) never count (see ``TimeService``).
"""

from __future__ import annotations

import calendar
import re
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select

from app.core.ai.tools import AIToolSpec, Source, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.time.models import TimeEntry

#: Entries returned by ``time.summary`` are capped — the total is summed in SQL either way.
_SUMMARY_ENTRY_CAP = 50

_PERIOD_RE = re.compile(r"(\d{4})-(0[1-9]|1[0-2])")


# --- argument parsing (model-supplied, so never trusted) --------------------- #
def _invalid() -> AppError:
    return AppError("validation", "errors.validation", status_code=422)


def _parse_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        raise _invalid() from None


def _parse_date(value: Any) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _invalid() from None


def _parse_date_or_none(value: Any) -> date | None:
    return None if value is None else _parse_date(value)


def _month_bounds(value: Any) -> tuple[str, date, date]:
    """A ``YYYY-MM`` period as (normalized string, first day, last day); else a 422."""
    match = _PERIOD_RE.fullmatch(str(value or ""))
    if match is None:
        raise _invalid()
    year, month = int(match[1]), int(match[2])
    last = date(year, month, calendar.monthrange(year, month)[1])
    return match[0], date(year, month, 1), last


# --- shared window / aggregation --------------------------------------------- #
def _window(date_from: date | None, date_to: date | None) -> list[Any]:
    """Interpret dates against ``started_at``. Times are wall-clock-as-UTC, so a plain
    ``[from 00:00Z, to+1day 00:00Z)`` window matches what the timesheet shows."""
    conditions: list[Any] = []
    if date_from is not None:
        conditions.append(TimeEntry.started_at >= datetime.combine(date_from, time.min, tzinfo=UTC))
    if date_to is not None:
        conditions.append(
            TimeEntry.started_at
            < datetime.combine(date_to, time.min, tzinfo=UTC) + timedelta(days=1)
        )
    return conditions


async def _company_minutes(
    ctx: RequestContext,
    company_id: uuid.UUID,
    date_from: date | None,
    date_to: date | None,
) -> dict[str, Any]:
    """Worked minutes logged against a company (all users), grouped per project in SQL."""
    ctx.require("time.report.read")
    minutes_sum = func.coalesce(func.sum(TimeEntry.minutes), 0).label("minutes")
    billable_sum = func.coalesce(
        func.sum(TimeEntry.minutes).filter(TimeEntry.billable.is_(True)), 0
    ).label("billable_minutes")
    stmt = (
        select(TimeEntry.project_id, minutes_sum, billable_sum)
        .where(
            TimeEntry.org_id == ctx.org.id,
            TimeEntry.company_id == company_id,
            TimeEntry.ended_at.is_not(None),  # a running timer burns nothing
            *_window(date_from, date_to),
        )
        .group_by(TimeEntry.project_id)
        .order_by(minutes_sum.desc())
    )
    rows = (await ctx.session.execute(stmt)).all()
    return {
        "minutes": sum(int(r[1]) for r in rows),
        "billable_minutes": sum(int(r[2]) for r in rows),
        "by_project": [
            {"project_id": str(r[0]) if r[0] is not None else None, "minutes": int(r[1])}
            for r in rows
        ],
    }


def _report_source(company_id: uuid.UUID) -> tuple[Source, ...]:
    return (Source(type="time_report", id=str(company_id), label=""),)


# --- handlers ----------------------------------------------------------------- #
async def _summary(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    """The **current user's own** entries — never anyone else's, whatever scope they hold."""
    ctx.require("time.entry.read")
    date_from = _parse_date(args.get("date_from"))
    date_to = _parse_date(args.get("date_to"))
    conditions = [
        TimeEntry.org_id == ctx.org.id,
        TimeEntry.user_id == ctx.user.id,
        TimeEntry.ended_at.is_not(None),
        *_window(date_from, date_to),
    ]
    total = int(
        await ctx.session.scalar(
            select(func.coalesce(func.sum(TimeEntry.minutes), 0)).where(*conditions)
        )
        or 0
    )
    rows = (
        (
            await ctx.session.execute(
                select(TimeEntry)
                .where(*conditions)
                .order_by(TimeEntry.started_at.desc())
                .limit(_SUMMARY_ENTRY_CAP)
            )
        )
        .scalars()
        .all()
    )
    entries = [
        {
            "id": str(e.id),
            "company_id": str(e.company_id) if e.company_id is not None else None,
            "project_id": str(e.project_id) if e.project_id is not None else None,
            "task_id": str(e.task_id) if e.task_id is not None else None,
            "description": e.description,
            "minutes": e.minutes,
            "date": e.started_at.astimezone(UTC).date().isoformat(),
        }
        for e in rows
    ]
    return ToolResult(data={"minutes": total, "entries": entries})


async def _for_company(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    company_id = _parse_uuid(args.get("company_id"))
    date_from = _parse_date_or_none(args.get("date_from"))
    date_to = _parse_date_or_none(args.get("date_to"))
    data = await _company_minutes(ctx, company_id, date_from, date_to)
    return ToolResult(data=data, sources=_report_source(company_id))


async def _company_month(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    company_id = _parse_uuid(args.get("company_id"))
    period, first, last = _month_bounds(args.get("period"))
    data = await _company_minutes(ctx, company_id, first, last)
    return ToolResult(data={"period": period, **data}, sources=_report_source(company_id))


TIME_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="time.summary",
        description=(
            "Summarise the current user's OWN logged time between two dates (inclusive). "
            "Call this when the user asks about their own hours, e.g. 'how much did I work "
            "this week?'. Returns total worked minutes and up to 50 entries with their "
            "date, client, project, task and description. Never returns other people's "
            "time; use time.for_company for team-wide questions."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "date_from": {"type": "string", "description": "YYYY-MM-DD"},
                "date_to": {"type": "string", "description": "YYYY-MM-DD inclusive"},
            },
            "required": ["date_from", "date_to"],
            "additionalProperties": False,
        },
        handler=_summary,
        permission="time.entry.read",
    ),
    AIToolSpec(
        name="time.for_company",
        description=(
            "Total worked minutes the whole team logged against one company, with a "
            "per-project breakdown. Call this when the user asks how many hours went into "
            "a client. Dates are optional and inclusive; omit both for all-time. Hours "
            "only — this tool never reports rates or money."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "date_from": {"type": ["string", "null"]},
                "date_to": {"type": ["string", "null"]},
            },
            "required": ["company_id"],
            "additionalProperties": False,
        },
        handler=_for_company,
        permission="time.report.read",
    ),
    AIToolSpec(
        name="time.company_month",
        description=(
            "Worked minutes logged against one company in one calendar month (period "
            "YYYY-MM), with a per-project breakdown. Call this when assembling a monthly "
            "client report or when the user asks about a specific month's hours for a "
            "client. Hours only — never rates or money."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "company_id": {"type": "string"},
                "period": {"type": "string", "description": "YYYY-MM"},
            },
            "required": ["company_id", "period"],
            "additionalProperties": False,
        },
        handler=_company_month,
        permission="time.report.read",
    ),
]
