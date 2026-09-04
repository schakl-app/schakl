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
ENTITY_INTERACTION = "interaction"
#: A connected accounting administration (#377). Not a record anybody opens — which is exactly
#: why the event that names it must hint its recipients rather than rely on watchers.
ENTITY_SNELSTART_ACCOUNT = "snelstart_account"

ENTITY_TYPES: tuple[str, ...] = (
    ENTITY_TASK,
    ENTITY_PROJECT,
    ENTITY_COMPANY,
    ENTITY_LEAVE,
    ENTITY_TIMESHEET,
    ENTITY_INTERACTION,
    ENTITY_SNELSTART_ACCOUNT,
)

# --- event types ------------------------------------------------------------------------- #
# tasks
TASK_ASSIGNED = "task.assigned"
TASK_UNASSIGNED = "task.unassigned"
TASK_STATUS_CHANGED = "task.status_changed"
TASK_COMMENTED = "task.commented"
# Someone answered a comment in a thread you are in (#312) — the root's author and everyone who
# has replied in it. Deliberately narrower than TASK_COMMENTED, which the same write still sends
# to the rest of the task's audience: being answered and being told a task was commented on are
# different sentences, and a recipient hears exactly one of them.
TASK_REPLIED = "task.replied"
TASK_MENTIONED = "task.mentioned"
#: The sentences a **client's** contact person may be told through their portal login: they
#: were named in a comment, or a task assigned to them was commented on / answered. Everything
#: else is the agency talking to itself. Stated as a closed set so a new staff event can never
#: reach a client by riding a hint.
PORTAL_EVENTS: frozenset[str] = frozenset({TASK_COMMENTED, TASK_REPLIED, TASK_MENTIONED})
TASK_DUE_SOON = "task.due_soon"
TASK_OVERDUE = "task.overdue"
# A task planned onto someone's calendar (#188). Recipient = the person the block is for; the
# actor (the scheduler) is auto-excluded, so planning your own task is silent.
TASK_SCHEDULED = "task.scheduled"
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
# interactions (#146): a matched Gmail message awaiting the mailbox owner's review. The gmail
# feed ingests it directly (owner-routed, deduped per message) — it sits in the matrix so the
# owner can retune cadence/channels, immediate by default (a review queue is not tomorrow's
# news). The constant in ``google/gmail/service.py`` (``PENDING_EVENT``) must match.
INTERACTION_EMAIL_PENDING = "interactions.email_pending"
# @mentioned in a contactmoment note (#151, like task.mentioned). Emitted by the interactions
# service (``MENTIONED_EVENT`` there must match), recipients = the newly mentioned users.
INTERACTION_MENTIONED = "interactions.mentioned"
# automation (issue #27): a rule's ``notification.send`` action. Not in EVENT_TYPES — it is
# ingested directly through this module's published service (its entity type varies per run,
# so the static subscribe/ENTITY_FOR_EVENT path cannot carry it), and it has no place in the
# per-event preference matrix: switching a rule off is the rule editor's job.
AUTOMATION_NOTIFY = "automation.notify"

#: Every notifiable event, in display order. The settings matrix renders exactly this list.
# snelstart (#377): an unattended finance sync that did not do what it set out to. Registered
# here like every other event so it appears in the preferences matrix — an agency that has
# decided to watch its ledger on a screen instead can switch it off, which is not the same thing
# as it never having been offered.
SNELSTART_SYNC_FAILED = "snelstart.sync.failed"

EVENT_TYPES: tuple[str, ...] = (
    TASK_ASSIGNED,
    TASK_UNASSIGNED,
    TASK_STATUS_CHANGED,
    TASK_COMMENTED,
    TASK_REPLIED,
    TASK_MENTIONED,
    TASK_DUE_SOON,
    TASK_OVERDUE,
    TASK_SCHEDULED,
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
    INTERACTION_EMAIL_PENDING,
    INTERACTION_MENTIONED,
    SNELSTART_SYNC_FAILED,
)

#: Which entity type each event attaches to (for the activity feed grouping + link target).
ENTITY_FOR_EVENT: dict[str, str] = {
    TASK_ASSIGNED: ENTITY_TASK,
    TASK_UNASSIGNED: ENTITY_TASK,
    TASK_STATUS_CHANGED: ENTITY_TASK,
    TASK_COMMENTED: ENTITY_TASK,
    TASK_REPLIED: ENTITY_TASK,
    TASK_MENTIONED: ENTITY_TASK,
    TASK_DUE_SOON: ENTITY_TASK,
    TASK_OVERDUE: ENTITY_TASK,
    TASK_SCHEDULED: ENTITY_TASK,
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
    INTERACTION_EMAIL_PENDING: ENTITY_INTERACTION,
    INTERACTION_MENTIONED: ENTITY_INTERACTION,
    SNELSTART_SYNC_FAILED: ENTITY_SNELSTART_ACCOUNT,
}

# --- channels ---------------------------------------------------------------------------- #
CHANNEL_IN_APP = "in_app"
#: Personal e-mail delivery through the org transport (Instellingen → E-mail, #17). One
#: *general* preference row per user (no per-event matrix): off, immediate, or a digest.
CHANNEL_EMAIL = "email"
#: Any configured external transport (Apprise — Slack, Teams, Discord, a webhook; #17). Unlike
#: the two above it is not implicit: each one is a ``notification_channels`` row, and a
#: *personal* one carries its own per-event preference rows keyed by ``channel_config_id`` (#283).
CHANNEL_EXTERNAL = "external"
#: The browser's own notifications (Web Push, #309) — implicit like in-app and e-mail: every
#: member has it, there is nothing to connect. What *is* per device is the subscription, which
#: lives in ``push_subscriptions`` rather than in ``notification_channels``: a browser mints it,
#: it rotates, and it dies with a ``410`` — none of which is true of a URL somebody typed.
CHANNEL_WEB_PUSH = "web_push"

#: Reserved payload keys the emitter uses to carry recipients/dedup to the subscriber. They
#: are stripped before the event row is persisted (they are routing, not content).
RECIPIENTS_KEY = "_recipients"
DEDUP_KEY = "_dedup_key"
#: People who are hearing a *more specific* sentence about this same write, and must therefore
#: not hear the general one (#312). Leaving them out of ``_recipients`` is not enough: the
#: dispatcher unions in the record's **watchers**, and the people a narrower event is aimed at
#: are exactly the ones most likely to be watching — someone who has commented on a task is
#: auto-watching it. So "you said it another way" has to be stated to the dispatcher, not merely
#: implied by an omission. Subtracted after the watcher union, before the actor is dropped.
EXCLUDE_KEY = "_exclude"
#: Logins from **outside** the agency this write is addressed to by name — a client's contact
#: person mentioned in a comment, or the contact a commented task is assigned to. Staff events
#: never reach a client's inbox (``_members_only``), and that stays the rule for ``_recipients``
#: and for watchers; this key is the emitter saying "this sentence is *for* them", and it is
#: honoured only for :data:`PORTAL_EVENTS`. A hint is still data from another module: the
#: dispatcher keeps only ids that are memberships of this org.
EXTERNAL_RECIPIENTS_KEY = "_external_recipients"

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
