"""microsoft.calendar — pull sync into the Agenda + one-way mirror push (docs/MICROSOFT.md §4).

Importing this package wires the mirror events onto the bus — the same seven the Google
integration subscribes, so approved leave, planned task blocks and freelance availability land
in the person's Outlook calendar exactly as they land in a Google one — and registers the
cached diary as the busy seam's third.
"""

from __future__ import annotations

from app.core.busy import register_busy_provider
from app.core.events import subscribe
from app.integrations.microsoft.calendar.busy import microsoft_calendar_busy
from app.integrations.microsoft.calendar.push import (
    handle_availability_gone,
    handle_availability_saved,
    handle_leave_approved,
    handle_leave_gone,
    handle_task_schedule_gone,
    handle_task_schedule_saved,
)

subscribe("leave.approved", handle_leave_approved)
# An approver's in-place edit of approved leave: the snapshot refreshes and the worker patches
# the stored event instead of leaving it on the old dates.
subscribe("leave.updated", handle_leave_approved)
subscribe("leave.cancelled", handle_leave_gone)
subscribe("leave.rejected", handle_leave_gone)
subscribe("leave.requested", handle_leave_gone)

# Task scheduling: a saved block pushes/refreshes; a removed block deletes.
subscribe("task_schedule.saved", handle_task_schedule_saved)
subscribe("task_schedule.removed", handle_task_schedule_gone)

# Freelance availability: one exception row ↔ one event, a repeat as a recurrence.
subscribe("availability.saved", handle_availability_saved)
subscribe("availability.gone", handle_availability_gone)

# The scheduling dialog's conflict check (app/core/busy.py): the cached mirror of a person's
# diary, titled only for its owner — Outlook's own free/busy rule.
register_busy_provider("microsoft.calendar", microsoft_calendar_busy)
