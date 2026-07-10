"""Event + entity vocabulary for the notifications module (issue #16).

The single source of truth for which events exist, which entity type each attaches to, and
which channels are defined. Emitting modules use the ``*_*`` string constants; the fan-out
subscriber and the preferences matrix iterate ``EVENT_TYPES``. Every ``event_type`` has a
matching ``notifications.event.<type>`` (feed sentence) and ``notifications.event_label.<type>``
(settings row) i18n key in both locales.
"""

from __future__ import annotations

# --- entity types (the polymorphic ``entity_type`` column) ------------------------------- #
ENTITY_TASK = "task"
ENTITY_PROJECT = "project"
ENTITY_COMPANY = "company"
ENTITY_LEAVE = "leave_request"
ENTITY_TIMESHEET = "timesheet"

ENTITY_TYPES: tuple[str, ...] = (
    ENTITY_TASK,
    ENTITY_PROJECT,
    ENTITY_COMPANY,
    ENTITY_LEAVE,
    ENTITY_TIMESHEET,
)

# --- event types ------------------------------------------------------------------------- #
# tasks
TASK_ASSIGNED = "task.assigned"
TASK_UNASSIGNED = "task.unassigned"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_COMMENTED = "task.commented"
TASK_DUE_SOON = "task.due_soon"
TASK_OVERDUE = "task.overdue"
# projects
PROJECT_ASSIGNED = "project.assigned"
PROJECT_STATUS_CHANGED = "project.status_changed"
PROJECT_BUDGET_THRESHOLD = "project.budget_threshold"
# companies
COMPANY_CREATED = "company.created"
COMPANY_STATUS_CHANGED = "company.status_changed"
COMPANY_ASSIGNED = "company.assigned"
# leave
LEAVE_REQUESTED = "leave.requested"
LEAVE_APPROVED = "leave.approved"
LEAVE_REJECTED = "leave.rejected"
# time
TIME_ENTRY_APPROVED = "time.entry_approved"
TIME_TIMESHEET_REMINDER = "time.timesheet_reminder"

#: Every notifiable event, in display order. The settings matrix renders exactly this list.
EVENT_TYPES: tuple[str, ...] = (
    TASK_ASSIGNED,
    TASK_UNASSIGNED,
    TASK_STATUS_CHANGED,
    TASK_COMMENTED,
    TASK_DUE_SOON,
    TASK_OVERDUE,
    PROJECT_ASSIGNED,
    PROJECT_STATUS_CHANGED,
    PROJECT_BUDGET_THRESHOLD,
    COMPANY_CREATED,
    COMPANY_STATUS_CHANGED,
    COMPANY_ASSIGNED,
    LEAVE_REQUESTED,
    LEAVE_APPROVED,
    LEAVE_REJECTED,
    TIME_ENTRY_APPROVED,
    TIME_TIMESHEET_REMINDER,
)

#: Which entity type each event attaches to (for the activity feed grouping + link target).
ENTITY_FOR_EVENT: dict[str, str] = {
    TASK_ASSIGNED: ENTITY_TASK,
    TASK_UNASSIGNED: ENTITY_TASK,
    TASK_STATUS_CHANGED: ENTITY_TASK,
    TASK_COMMENTED: ENTITY_TASK,
    TASK_DUE_SOON: ENTITY_TASK,
    TASK_OVERDUE: ENTITY_TASK,
    PROJECT_ASSIGNED: ENTITY_PROJECT,
    PROJECT_STATUS_CHANGED: ENTITY_PROJECT,
    PROJECT_BUDGET_THRESHOLD: ENTITY_PROJECT,
    COMPANY_CREATED: ENTITY_COMPANY,
    COMPANY_STATUS_CHANGED: ENTITY_COMPANY,
    COMPANY_ASSIGNED: ENTITY_COMPANY,
    LEAVE_REQUESTED: ENTITY_LEAVE,
    LEAVE_APPROVED: ENTITY_LEAVE,
    LEAVE_REJECTED: ENTITY_LEAVE,
    TIME_ENTRY_APPROVED: ENTITY_TIMESHEET,
    TIME_TIMESHEET_REMINDER: ENTITY_TIMESHEET,
}

# --- channels ---------------------------------------------------------------------------- #
CHANNEL_IN_APP = "in_app"

#: Reserved payload keys the emitter uses to carry recipients/dedup to the subscriber. They
#: are stripped before the event row is persisted (they are routing, not content).
RECIPIENTS_KEY = "_recipients"
DEDUP_KEY = "_dedup_key"

# --- digest cadences --------------------------------------------------------------------- #
DIGEST_IMMEDIATE = "immediate"
DIGEST_HOURLY = "hourly"
DIGEST_DAILY = "daily"
DIGEST_WEEKLY = "weekly"
DIGEST_CADENCES: tuple[str, ...] = (
    DIGEST_IMMEDIATE,
    DIGEST_HOURLY,
    DIGEST_DAILY,
    DIGEST_WEEKLY,
)
