"""google.calendar — pull sync into the Agenda + one-way leave push (docs/GOOGLE.md §4).

Importing this package wires the leave events onto the bus. The four subscriptions cover the
whole lifecycle of an approved request: approval pushes, cancellation and a rejected bounce
delete, and an edit that bounces an approved request back to pending deletes too (the
re-approval then pushes the corrected span).
"""

from __future__ import annotations

from app.core.events import subscribe
from app.modules.google.calendar.push import handle_leave_approved, handle_leave_gone

subscribe("leave.approved", handle_leave_approved)
# An approver's in-place edit of approved leave (#148): same handler — the snapshot
# refreshes and the worker PUTs the stored event instead of leaving it on the old dates.
subscribe("leave.updated", handle_leave_approved)
subscribe("leave.cancelled", handle_leave_gone)
subscribe("leave.rejected", handle_leave_gone)
subscribe("leave.requested", handle_leave_gone)
