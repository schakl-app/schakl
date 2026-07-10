"""Shared API schemas (CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict

T = TypeVar("T")


class AssigneeWrite(BaseModel):
    """An employee assigned to a company or project; ``is_primary`` stars the responsible one.

    A list with no star promotes its first entry — the picker's own default.
    """

    user_id: uuid.UUID
    is_primary: bool = False


class AssigneeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    is_primary: bool


class BudgetHours(BaseModel):
    """A project's budget burn for the current period (#25). Opt-in — see ``hours=`` on the list.

    ``spent_hours`` counts **all** logged hours, billable or not: internal work on a client's
    project still consumes its budget. ``billable_hours`` and ``unapproved_hours`` are subsets of
    it, so the UI can qualify a number rather than show a different one.
    """

    period: str                       # total | monthly | weekly | daily
    period_start: date | None = None  # None for a "total" budget — it never resets
    budget_hours: float | None = None  # None ⇒ no budget; there is nothing to remain
    spent_hours: float = 0.0
    billable_hours: float = 0.0
    unapproved_hours: float = 0.0
    remaining_hours: float | None = None  # may be negative — over budget is not clamped


class CompanyBudgetHours(BaseModel):
    """A client's budget burn, rolled up from its **active projects that have a budget**.

    Hours on the client's other projects, or logged straight to the client, have no allowance to
    burn against. Counting them would make ``budget − spent`` stop matching the number on screen,
    so they are reported separately as ``unbudgeted_hours`` — never silently dropped, never folded
    into the bar. A client with no budgeted project has ``budget_hours: None``: an em-dash, not a
    fabricated total.
    """

    period: str | None = None  # None ⇒ the client's projects mix budget periods
    budget_hours: float | None = None
    spent_hours: float = 0.0
    billable_hours: float = 0.0
    unapproved_hours: float = 0.0
    remaining_hours: float | None = None
    unbudgeted_hours: float = 0.0
    project_count: int = 0  # budgeted active projects behind these figures


class Page(BaseModel, Generic[T]):
    """page/limit pagination envelope."""

    items: list[T]
    total: int
    limit: int
    offset: int


class PanelData(BaseModel):
    """One composed panel on a host entity's detail view (the "attach to company" hub)."""

    key: str
    title_key: str          # i18n key
    position: int
    data: dict[str, Any]
