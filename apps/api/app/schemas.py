"""Shared API schemas (CLAUDE.md §9)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

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
    #: "Working surface" or "register" (#364) — the module's own answer, since only it knows
    #: whether its card is something the reader acts on today or reference material.
    prominence: str = "register"
    #: Preferred width in the host's desktop grid (#364): ``full`` or ``half``.
    size: str = "full"
    #: This client has nothing here yet (#364). The page absorbs such panels into one strip of
    #: ＋ chips rather than drawing ten headings over ten negative sentences. ``False`` for a
    #: panel that declares no predicate — silence is never read as emptiness.
    empty: bool = False


class SummaryData(BaseModel):
    """One vital sign in a host entity's header strip (#364).

    The panels answer "what is on file"; these answer *"are we all right with this client"* —
    and each one opens the thing it counted (docs/UX.md principle 7, "every number opens").
    """

    key: str
    label_key: str          # i18n key
    #: Raw: a decimal string, an integer, an ISO date or free text — the reader's locale
    #: formats it (§8), so this never carries a currency symbol or a decimal comma.
    value: str
    format: str = "number"  # money | number | hours | date | text
    currency: str | None = None
    tone: str = "neutral"   # neutral | good | warn | bad
    hint_key: str | None = None
    hint_params: dict[str, Any] = Field(default_factory=dict)
    href: str | None = None
    position: int = 100
