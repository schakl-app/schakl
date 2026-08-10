"""Pydantic schemas for the projects module (CLAUDE.md §6, §9).

Budgets are exposed as floats (planning figures) even though stored as ``Numeric``. There is
no project ``hourly_rate`` (#226): money is always priced at the rate of the employee who
logged the time (leave profile → org default).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.projects.models import ProjectStatus
from app.schemas import AssigneeRead, AssigneeWrite, BudgetHours


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
    currency: str = Field(default="EUR", min_length=3, max_length=3)
    color: str | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    custom: dict = Field(default_factory=dict)


class ProjectCreate(ProjectBase):
    # A project is work done *for* somebody, so it is created for a client and never floats
    # unattached: the budget, the hours it prices, the invoice it ends up on and the panel it
    # appears under all read this. Narrowed from the base rather than declared there, because
    # ``ProjectRead`` shares that base and rows predating the rule keep an honest ``None``
    # (the column stays nullable — see the update rule in ``service.py``).
    company_id: uuid.UUID
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
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    color: str | None = Field(default=None, max_length=20)
    start_date: date | None = None
    end_date: date | None = None
    custom: dict | None = None


class ProjectHoursSource(BaseModel):
    """A subscription this project's hour budget derives from (issue #225).

    ``included_hours`` is per the subscription's own billing interval; ``monthly_hours`` is
    its monthly equivalent — the figure the derived budget sums.
    """

    model_config = ConfigDict(from_attributes=True)

    subscription_id: uuid.UUID
    name: str
    included_hours: float
    monthly_hours: float


class ProjectRead(ProjectBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    created_at: datetime
    updated_at: datetime
    # The client's name, resolved for the page in one batched query (`hosting`/`domains` do the
    # same). A row carries what its screen draws (CLAUDE.md §9): the list sections *by* client,
    # and a browser resolving the name against a capped picker list prints "—" for every client
    # past the cap — which, once the list is sectioned, silently drops those projects into
    # "Overig". ``None`` only for the rows predating "a project has a client".
    company_name: str | None = None
    # Primary first, then oldest assignment first.
    assignees: list[AssigneeRead] = Field(default_factory=list)
    # Budget burn for the current period. Only present when asked for (``?hours=true``).
    hours: BudgetHours | None = None
    # Non-empty ⇒ ``budget_hours`` is subscription-backed: the effective budget is the sum of
    # ``monthly_hours``, the period is forced to monthly, and direct writes are refused (#225).
    # Populated on the detail read and wherever ``hours`` is; the stored ``budget_hours`` stays
    # visible as the dormant fallback that returns when the link is removed.
    budget_sources: list[ProjectHoursSource] = Field(default_factory=list)


class DashboardBudgetProject(BaseModel):
    """The four fields the My Day burn tile draws — nothing else (#290).

    The widget used to request 200 active projects with full budget enrichment, then keep the
    hottest four. This is the same aggregate, sorted and cut server-side.
    """

    id: uuid.UUID
    name: str
    hours: BudgetHours
