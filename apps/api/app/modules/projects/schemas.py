"""Pydantic schemas for the projects module (CLAUDE.md §6, §9).

Budgets/rate are exposed as floats (planning figures) even though stored as ``Numeric``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.projects.models import ProjectStatus
from app.schemas import AssigneeRead, AssigneeWrite


class ProjectBase(BaseModel):
    company_id: uuid.UUID | None = None
    # The primary assignee, mirrored from ``assignees``. Inherited from the company's primary on
    # create when neither is given (see service).
    responsible_user_id: uuid.UUID | None = None
    name: str = Field(min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus = ProjectStatus.ACTIVE
    billable_default: bool = True
    budget_period: str = Field(default="total", pattern="^(total|monthly|weekly|daily)$")
    budget_hours: float | None = Field(default=None, ge=0)
    budget_amount: float | None = Field(default=None, ge=0)
    hourly_rate: float | None = Field(default=None, ge=0)
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    color: str | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    custom: dict = Field(default_factory=dict)


class ProjectCreate(ProjectBase):
    # ``None`` (not ``[]``) means the caller didn't say: fall back to ``responsible_user_id``,
    # else inherit the company's primary.
    assignees: list[AssigneeWrite] | None = None


class ProjectUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
    assignees: list[AssigneeWrite] | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    status: ProjectStatus | None = None
    billable_default: bool | None = None
    budget_period: str | None = Field(default=None, pattern="^(total|monthly|weekly|daily)$")
    budget_hours: float | None = Field(default=None, ge=0)
    budget_amount: float | None = Field(default=None, ge=0)
    hourly_rate: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    color: str | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    custom: dict | None = None


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # Primary first, then oldest assignment first.
    assignees: list[AssigneeRead] = Field(default_factory=list)
