"""Pydantic schemas for the tasks module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.tasks.models import TaskPriority, TaskStatus


class TaskBase(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    status: TaskStatus = TaskStatus.OPEN
    priority: TaskPriority = TaskPriority.NORMAL
    due_date: date | None = None


class TaskCreate(TaskBase):
    pass


class TaskUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: date | None = None


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
