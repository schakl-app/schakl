"""Executable AI tools this module contributes (CLAUDE.md §6, §12, §14; issue #129).

Day reconstruction measures "short" against the **work schedule**, never against an assumed
9-to-5: a three-day part-timer's Wednesday is not missing hours, and neither is Eerste
Kerstdag or an approved afternoon of vacation. This tool hands the assistant that yardstick
for one date. Read-only, and the handler runs through :class:`LeaveService` under the
caller's :class:`RequestContext` — it can never answer beyond the caller's tenant (Golden
Rule 1) or role (§15), and it only ever describes the *current* user's own day.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.ai import AIToolSpec, ToolResult
from app.core.tenancy import RequestContext
from app.errors import AppError
from app.modules.leave import schedule as sched
from app.modules.leave.models import LeaveRequest, LeaveRequestStatus
from app.modules.leave.service import LeaveService


async def _day_schedule(ctx: RequestContext, args: dict[str, Any]) -> ToolResult:
    try:
        day = date.fromisoformat(str(args.get("date")))
    except (TypeError, ValueError):
        raise AppError("validation", "errors.validation", status_code=422) from None

    service = LeaveService(ctx)
    # The same seams §14's hour calculation reads: the employee's effective schedule (own,
    # else the org default) and the org's active holiday calendar (#46, #47).
    schedule = await service.effective_schedule(ctx.user.id)
    holidays_off = await service.active_holidays_between(day, day)
    holiday = day in holidays_off
    scheduled_minutes = 0 if holiday else sched.day_minutes(schedule.day(day.weekday()))

    requests = (
        (
            await ctx.session.execute(
                service.requests.scoped_select().where(
                    LeaveRequest.user_id == ctx.user.id,
                    LeaveRequest.status == LeaveRequestStatus.APPROVED.value,
                    LeaveRequest.start_date <= day,
                    LeaveRequest.end_date >= day,
                )
            )
        )
        .scalars()
        .all()
    )
    # Per-day minutes via the service's own breakdown — the requested window intersected with
    # the scheduled day, minus every break it overlaps (#48). A request's boundary times only
    # bind on its boundary days; a middle day is covered whole. Never the total spread evenly.
    leave_minutes = 0
    for request in requests:
        rows = service._breakdown(
            schedule=schedule,
            holidays_off=holidays_off,
            start_date=day,
            start_time=request.start_time if request.start_date == day else None,
            end_date=day,
            end_time=request.end_time if request.end_date == day else None,
        )
        leave_minutes += rows[0][1]

    return ToolResult(
        data={
            "date": day.isoformat(),
            "scheduled_minutes": scheduled_minutes,
            "leave_minutes": leave_minutes,
            "holiday": holiday,
        }
    )


LEAVE_MCP_TOOLS: list[AIToolSpec] = [
    AIToolSpec(
        name="leave.day_schedule",
        description=(
            "The current user's scheduled working minutes and approved leave minutes for one "
            "calendar date, from their work schedule and the workspace's holiday calendar. "
            "Call this to know how long their workday is before judging missing hours: 0 "
            "scheduled_minutes means no workday at all (weekend, part-time day or holiday), "
            "and leave_minutes are hours that are legitimately absent."
        ),
        input_schema={
            "type": "object",
            "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
            "required": ["date"],
            "additionalProperties": False,
        },
        handler=_day_schedule,
        permission="leave.request.read",
    ),
]
