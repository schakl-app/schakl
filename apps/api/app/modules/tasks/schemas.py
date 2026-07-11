"""Pydantic schemas for the tasks module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.tasks.models import (
    RecurrenceFreq,
    RecurrenceMode,
    TaskPriority,
    TaskStatus,
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
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    status: TaskStatus = TaskStatus.OPEN
    priority: TaskPriority = TaskPriority.NORMAL
    due_date: date | None = None
    allocated_minutes: int | None = Field(default=None, ge=0, le=100000)


class TaskCreate(TaskBase):
    recurrence: Recurrence | None = None


class TaskUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None
    allocated_minutes: int | None = Field(default=None, ge=0, le=100000)
    position: float | None = None
    recurrence: Recurrence | None = None
    # Required when the due date moves later (accountability; logged in the activity feed).
    due_change_reason: str | None = Field(default=None, max_length=1000)


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    position: float
    completed_at: datetime | None
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
