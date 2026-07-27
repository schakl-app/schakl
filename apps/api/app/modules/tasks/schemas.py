"""Pydantic schemas for the tasks module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.tasks.models import (
    RecurrenceFreq,
    RecurrenceMode,
    TaskPriority,
    TemplateTrigger,
)


class Recurrence(BaseModel):
    freq: RecurrenceFreq
    interval: int = Field(default=1, ge=1, le=365)
    mode: RecurrenceMode = RecurrenceMode.AFTER_COMPLETION


class TaskBase(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    # A task's client contact as assignee (#273) — mutually exclusive with ``assignee_user_id``
    # and scoped to ``company_id`` server-side. Set exactly one; a contact never coexists with an
    # employee assignee.
    assignee_contact_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    # A tenant-configured status key (issue #62); ``None`` on create means the org's default
    # status. Validated against the org's ``task_statuses`` in the service.
    status: str | None = Field(default=None, max_length=50)
    priority: TaskPriority = TaskPriority.NORMAL
    due_date: date | None = None
    allocated_minutes: int | None = Field(default=None, ge=0, le=100000)
    # Per-task close policy (#157 extended): when set, this task can only reach a finished
    # status once a designated closing contact moment is linked, regardless of the status flag.
    requires_interaction: bool = False
    # Client-portal visibility: off by default — staff opt a task in explicitly.
    visible_to_client: bool = False


class TaskCreate(TaskBase):
    recurrence: Recurrence | None = None


class TaskUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    # See ``TaskBase``: exclusive with ``assignee_user_id``, scoped to the task's company. The web
    # picker always posts both keys (one null) so switching kinds clears the other; a raw client
    # that sets this while leaving a live ``assignee_user_id`` gets the 422 exclusivity error.
    assignee_contact_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    status: str | None = Field(default=None, max_length=50)
    priority: TaskPriority | None = None
    due_date: date | None = None
    allocated_minutes: int | None = Field(default=None, ge=0, le=100000)
    position: float | None = None
    recurrence: Recurrence | None = None
    # Toggle the per-task "close only with a contact moment" policy (#157 extended).
    requires_interaction: bool | None = None
    visible_to_client: bool | None = None
    # Required when the due date moves later (accountability; logged in the activity feed).
    due_change_reason: str | None = Field(default=None, max_length=1000)
    # The contact moment this close is justified by (#157) — must be linked to this task and
    # team-visible; required by statuses flagged ``requires_interaction``.
    closing_interaction_id: uuid.UUID | None = None


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    # Always present on a stored task (the create default has been resolved to a real key).
    status: str
    position: float
    completed_at: datetime | None
    closing_interaction_id: uuid.UUID | None = None
    recurrence: Recurrence | None
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------------------- #
# Labels
# --------------------------------------------------------------------------- #
class LabelBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(min_length=1, max_length=20)
    position: int = 0


class LabelCreate(LabelBase):
    pass


class LabelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, min_length=1, max_length=20)
    position: int | None = None


class LabelRead(LabelBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID


class TaskLabelsSet(BaseModel):
    """PUT semantics: the task's label set becomes exactly these ids."""

    label_ids: list[uuid.UUID]


# --------------------------------------------------------------------------- #
# Statuses (org-level, tenant-configurable — issue #62)
# --------------------------------------------------------------------------- #
class StatusBase(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(min_length=1, max_length=20)
    position: int = 0
    # Finished states stamp ``completed_at`` and can spawn an after-completion recurrence.
    is_terminal: bool = False
    # The status a new task starts in. At most one per org (the service enforces exactly one).
    is_default: bool = False
    # Entering this status demands a designated closing contact moment (#157).
    requires_interaction: bool = False


class StatusCreate(StatusBase):
    # An immutable slug ``Task.status`` stores; only settable on create.
    key: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")


class StatusUpdate(BaseModel):
    # ``key`` is immutable (tasks reference it), so it is not updatable.
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, min_length=1, max_length=20)
    position: int | None = None
    is_terminal: bool | None = None
    is_default: bool | None = None
    requires_interaction: bool | None = None


class StatusRead(StatusBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str


# --------------------------------------------------------------------------- #
# List / detail composites
# --------------------------------------------------------------------------- #
class TaskListItem(TaskRead):
    """List-row shape: enough to render a card without loading the detail."""

    labels: list[LabelRead] = Field(default_factory=list)
    checklist_done: int = 0
    checklist_total: int = 0
    comment_count: int = 0


# --------------------------------------------------------------------------- #
# Checklists
# --------------------------------------------------------------------------- #
class ChecklistItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    # Markdown source, rendered sanitized by the web (issue #66); optional per item.
    description: str | None = None


class ChecklistItemUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=512)
    # ``exclude_unset`` distinguishes "not touched" from an explicit ``null`` that clears it.
    description: str | None = None
    done: bool | None = None
    position: int | None = None


class ChecklistItemRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None = None
    done: bool
    position: int


class ChecklistCreate(BaseModel):
    # Either a fresh checklist (title) or a copy of a template (template_id wins for content;
    # title still overrides the template's when given).
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    template_id: uuid.UUID | None = None


class ChecklistUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    position: int | None = None


class ChecklistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None = None
    position: int
    items: list[ChecklistItemRead] = Field(default_factory=list)


class TemplateChecklistItem(BaseModel):
    """One item of a checklist template — a title and an optional markdown description (issue #66).

    Reshaped from a bare ``str``; the API stores it in the ``*_rich`` columns and dual-writes the
    legacy title-only arrays for rollback safety (expand/contract, docs/WORKFLOW.md).
    """

    title: str = Field(min_length=1, max_length=512)
    description: str | None = None


class ChecklistTemplateBase(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    items: list[TemplateChecklistItem] = Field(default_factory=list, max_length=100)


class ChecklistTemplateCreate(ChecklistTemplateBase):
    pass


class ChecklistTemplateUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    items: list[TemplateChecklistItem] | None = Field(default=None, max_length=100)


class ChecklistTemplateRead(ChecklistTemplateBase):
    id: uuid.UUID


# --------------------------------------------------------------------------- #
# Comments & activity
# --------------------------------------------------------------------------- #
class CommentCreate(BaseModel):
    body: str = Field(min_length=1)


class CommentUpdate(BaseModel):
    body: str = Field(min_length=1)


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    author_user_id: uuid.UUID | None
    # The live account's name while it exists, else the snapshot taken when the comment was
    # written (issue #64). ``author_deleted`` says which — the UI marks a departed author
    # rather than dropping their name.
    author_name: str | None = None
    author_deleted: bool = False
    body: str
    # Users @mentioned in the body (issue #63), extracted from the markers on write.
    mentioned_user_ids: list[uuid.UUID] = Field(default_factory=list)
    # Contacts @mentioned (#165) — CRM references, never notification recipients.
    mentioned_contact_ids: list[uuid.UUID] = Field(default_factory=list)
    # Tasks #referenced (#197) — deep links into the board, validated org-scoped on write.
    mentioned_task_ids: list[uuid.UUID] = Field(default_factory=list)
    edited_at: datetime | None
    created_at: datetime


class ActivityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_user_id: uuid.UUID | None
    actor_name: str | None = None
    # A named actor with no live account is a deleted user; an unnamed one is the system
    # (the recurrence cron). Without this the two collapse into each other (issue #64).
    actor_deleted: bool = False
    action: str
    payload: dict[str, Any]
    created_at: datetime


class LinkCreate(BaseModel):
    url: str = Field(min_length=1, max_length=1024)
    title: str | None = Field(default=None, max_length=255)


class LinkRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    title: str | None


class TaskDetail(TaskRead):
    """The full "card": everything the task detail page renders."""

    labels: list[LabelRead] = Field(default_factory=list)
    checklists: list[ChecklistRead] = Field(default_factory=list)
    comments: list[CommentRead] = Field(default_factory=list)
    activities: list[ActivityRead] = Field(default_factory=list)
    links: list[LinkRead] = Field(default_factory=list)
    # Minutes booked on this task (from time tracking) — drives the budget colour.
    logged_minutes: int = 0


# --------------------------------------------------------------------------- #
# Templates
# --------------------------------------------------------------------------- #
class TemplateItemBase(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    priority: TaskPriority = TaskPriority.NORMAL
    relative_due_days: int | None = Field(default=None, ge=0, le=365)
    allocated_minutes: int | None = Field(default=None, ge=0, le=100000)
    assignee_user_id: uuid.UUID | None = None
    #: Assign to the company's primary responsible at apply time (#28); falls back to
    #: ``assignee_user_id``, then unassigned, when the company has none.
    assign_responsible: bool = False
    # Tasks spawned from this item may only be closed with a designated contact moment
    # (#157 extended); copied onto ``Task.requires_interaction`` at apply time.
    requires_interaction: bool = False
    position: int = 0
    checklist_title: str | None = Field(default=None, max_length=255)
    checklist_items: list[TemplateChecklistItem] = Field(default_factory=list)


class TemplateItemRead(TemplateItemBase):
    id: uuid.UUID


class TemplateBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    trigger: TemplateTrigger = TemplateTrigger.MANUAL
    trigger_status: str | None = Field(default=None, max_length=20)
    active: bool = True


class TemplateCreate(TemplateBase):
    items: list[TemplateItemBase] = Field(default_factory=list)


class TemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    trigger: TemplateTrigger | None = None
    trigger_status: str | None = Field(default=None, max_length=20)
    active: bool | None = None
    # When present, replaces the item list wholesale (simplest editor contract).
    items: list[TemplateItemBase] | None = None


class TemplateRead(TemplateBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    items: list[TemplateItemRead] = Field(default_factory=list)


class TemplateApply(BaseModel):
    company_id: uuid.UUID


# --------------------------------------------------------------------------- #
# Scheduling (#188) — planned time blocks for a task on a calendar
# --------------------------------------------------------------------------- #
# The client works in the org's *local* calendar — a day, a start time, and a length — and the
# API owns the timezone: it combines them into ``TIMESTAMPTZ`` instants (§8). This is why a
# day-drag stays DST-correct (the wall-clock time is preserved across the boundary) and why the
# browser never does timezone math. ``hours``/instants are never accepted from a client.
class ScheduleCreate(BaseModel):
    task_id: uuid.UUID
    # Omitted → the task's assignee (resolved server-side); an explicit value needs
    # ``tasks.schedule.write:any``.
    user_id: uuid.UUID | None = None
    # ``day`` not ``date``: a field named ``date`` shadows the imported ``date`` type when the
    # annotation is resolved, so the model won't build.
    day: date
    start_time: time
    duration_minutes: int = Field(ge=1, le=24 * 60)
    note: str | None = Field(default=None, max_length=500)


class ScheduleUpdate(BaseModel):
    """A partial edit / move: any omitted field keeps the block's current local value."""

    user_id: uuid.UUID | None = None
    day: date | None = None
    start_time: time | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    note: str | None = Field(default=None, max_length=500)


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_id: uuid.UUID
    user_id: uuid.UUID | None
    # Instants for the calendar's time grid; the edit form derives local date/time from these.
    starts_at: datetime
    ends_at: datetime
    note: str | None
    time_entry_id: uuid.UUID | None
    created_by_user_id: uuid.UUID | None
    created_by_name: str | None


class ScheduleItem(ScheduleRead):
    """A block decorated with what the calendar/timesheet needs, so a feed renders without a
    second fetch (docs/PERFORMANCE.md): the local day span (so the browser does no timezone
    math), the person's name and the task's identity."""

    # Inclusive local-date span for day bucketing, resolved in the org timezone server-side —
    # exactly like the Google events feed, so the calendar source maps 1:1.
    start: date
    end: date
    user_name: str | None = None
    task_title: str
    project_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    status: str
    allocated_minutes: int | None = None


class ScheduleLogTime(BaseModel):
    """Confirm-to-log a passed block as a real time entry (#188). Everything defaults from the
    block; the user may adjust the worked minutes, break, description and billable flag before
    saving. ``minutes`` overrides the block's own duration when the actual work differed."""

    minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    break_minutes: int = Field(default=0, ge=0, le=24 * 60)
    description: str | None = Field(default=None, max_length=2000)
    billable: bool = True
    entry_type_key: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
