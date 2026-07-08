"""Pydantic schemas for the projects module (CLAUDE.md §6, §9).

Budgets/rate are exposed as floats (planning figures) even though stored as ``Numeric``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.projects.models import ProjectStatus


class ProjectBase(BaseModel):
    company_id: uuid.UUID | None = None
    # Verantwoordelijke; defaults from the company on create when omitted (see service).
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
    pass


class ProjectUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    responsible_user_id: uuid.UUID | None = None
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
