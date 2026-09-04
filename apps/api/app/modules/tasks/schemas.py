"""Pydantic schemas for the tasks module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.tasks.models import (
    RecurrenceFreq,
    RecurrenceMode,
    TaskPriority,
    TemplateTrigger,
)
from app.schemas import AssigneeRead, AssigneeWrite

#: Where a planned block lands relative to the occurrence it belongs to (``PlanBlock.on``).
PLAN_PLACEMENTS = ("due", "offset", "weekday", "day")


class PlanBlock(BaseModel):
    """One block a spawned occurrence books itself — its **day** stated relative to the
    occurrence, its clock and length absolute, its people optional.

    A recurring job is rarely one sitting on the deadline: the newsletter is drafted on the
    Tuesday before, reviewed on the Thursday, sent on the first. So the day is a *placement*:

    * ``due`` — the occurrence's own due date (what every plan stored before this was);
    * ``offset`` — ``days`` before (negative) or after the due date;
    * ``weekday`` — a weekday: in the due date's own week when ``week`` is absent, else the
      ``week``-th such weekday of the due date's month (``-1`` for the last one);
    * ``day`` — day ``day`` of the due date's month, clamped like the anchors are.

    ``user_ids`` omitted means *the occurrence's own roster*, resolved at spawn time rather than
    frozen here — a recurring task whose assignees change must plan the new people's calendars,
    not whoever happened to be on it when the rule was written.
    """

    on: str = "due"
    days: int | None = Field(default=None, ge=-60, le=60)
    weekday: int | None = Field(default=None, ge=0, le=6)
    week: int | None = Field(default=None, ge=-1, le=4)
    day: int | None = Field(default=None, ge=1, le=31)
    user_ids: list[uuid.UUID] | None = Field(default=None, max_length=50)
    start_time: time
    duration_minutes: int = Field(ge=1, le=24 * 60)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def _placement_is_whole(self) -> PlanBlock:
        if self.on not in PLAN_PLACEMENTS:
            raise ValueError("errors.tasks_recurrence_plan:on")
        needed: dict[str, set[str]] = {
            "due": set(),
            "offset": {"days"},
            "weekday": {"weekday"},
            "day": {"day"},
        }[self.on]
        for field in ("days", "weekday", "day"):
            present = getattr(self, field) is not None
            if present != (field in needed):
                raise ValueError(f"errors.tasks_recurrence_plan:{field}")
        if self.on == "offset" and self.days == 0:
            raise ValueError("errors.tasks_recurrence_plan:days")
        if self.week is not None and (self.on != "weekday" or self.week == 0):
            raise ValueError("errors.tasks_recurrence_plan:week")
        return self


class RecurrencePlan(BaseModel):
    """"Herhaal ook de planning" (#335): what a spawned occurrence books onto a calendar.

    Two shapes, one meaning. The original carried a single clock — ``user_id``, ``start_time``,
    ``duration_minutes`` — for one block on the due date, and every rule stored that way keeps
    working unchanged. ``blocks`` is the same idea with the day made explicit and the count
    made plural (:class:`PlanBlock`); a plan with ``blocks`` ignores the legacy trio, and
    ``app.modules.tasks.recurrence.plan_blocks`` is the one reader that folds both into a list.
    """

    user_id: uuid.UUID | None = None
    start_time: time | None = None
    duration_minutes: int | None = Field(default=None, ge=1, le=24 * 60)
    blocks: list[PlanBlock] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def _one_shape_or_the_other(self) -> RecurrencePlan:
        if self.blocks:
            return self
        if self.start_time is None or self.duration_minutes is None:
            raise ValueError("errors.tasks_recurrence_plan:blocks")
        return self


class Recurrence(BaseModel):
    """A repeat rule, stored whole in ``tasks.recurrence`` (JSONB — no migration, #335).

    The anchors are **optional and absent by default**, which is what keeps every rule stored
    before #335 valid and unchanged: with none of them set, the cadence still hangs off the due
    date exactly as it did. Setting one pins the rhythm to a calendar the user can name — "elke
    maand op dag 1" rather than "a month after whatever the deadline happens to be", and since
    the plan grew placements, "elke maand op de tweede dinsdag" (``on_weekday`` + ``on_week``).

    Which anchor a frequency accepts is a property of the frequency, so a mismatched pair is a
    422 rather than a field silently ignored: a rule that says "weekly on day 15" and quietly
    repeats every Tuesday is worse than one that refuses to save.
    """

    freq: RecurrenceFreq
    interval: int = Field(default=1, ge=1, le=365)
    mode: RecurrenceMode = RecurrenceMode.AFTER_COMPLETION
    #: Weekly: the day. Monthly/quarterly/yearly: together with ``on_week``, the n-th such day
    #: of the month. ``date.weekday()`` numbering — Monday 0 … Sunday 6.
    on_weekday: int | None = Field(default=None, ge=0, le=6)
    #: Monthly/quarterly/yearly. Clamped to the month's length, so 31 lands on 28/29/30 Feb.
    on_day: int | None = Field(default=None, ge=1, le=31)
    #: Yearly only, and only together with ``on_day`` or with ``on_weekday`` + ``on_week``.
    on_month: int | None = Field(default=None, ge=1, le=12)
    #: Which ``on_weekday`` of the month: 1–4, or -1 for the last one. Never weekly.
    on_week: int | None = Field(default=None, ge=-1, le=4)
    #: Book each occurrence onto a calendar as it is created (#335, phase 5).
    plan: RecurrencePlan | None = None

    @model_validator(mode="after")
    def _anchors_match_freq(self) -> Recurrence:
        freq = self.freq.value
        monthly = freq in (
            RecurrenceFreq.MONTHLY.value,
            RecurrenceFreq.QUARTERLY.value,
            RecurrenceFreq.YEARLY.value,
        )
        if freq == RecurrenceFreq.DAILY.value:
            for field in ("on_weekday", "on_day", "on_month", "on_week"):
                if getattr(self, field) is not None:
                    raise ValueError(f"errors.tasks_recurrence_anchor:{field}")
            return self
        if freq == RecurrenceFreq.WEEKLY.value:
            for field in ("on_day", "on_month", "on_week"):
                if getattr(self, field) is not None:
                    raise ValueError(f"errors.tasks_recurrence_anchor:{field}")
            return self
        # Monthly, quarterly, yearly: a day-of-month *or* an n-th weekday, never both halves.
        if self.on_week == 0:
            raise ValueError("errors.tasks_recurrence_anchor:on_week")
        if (self.on_weekday is None) != (self.on_week is None):
            raise ValueError("errors.tasks_recurrence_anchor:on_week")
        if self.on_weekday is not None and self.on_day is not None:
            raise ValueError("errors.tasks_recurrence_anchor:on_day")
        if monthly and freq != RecurrenceFreq.YEARLY.value and self.on_month is not None:
            raise ValueError("errors.tasks_recurrence_anchor:on_month")
        if freq == RecurrenceFreq.YEARLY.value:
            # A year needs a whole date or none: "on day 15" with no month is not a date, and a
            # month with no day would have to invent one.
            anchored = self.on_day is not None or self.on_weekday is not None
            if anchored != (self.on_month is not None):
                raise ValueError("errors.tasks_recurrence_anchor:on_month")
        return self


class RecurrencePreview(BaseModel):
    """"Volgende taak: za 13 sep" — the number that will be stored, and why (#48's precedent).

    The editor composes a rule and asks the API what it resolves to, rather than re-deriving the
    dates in the browser: the arithmetic (clamping, leap years, the org's own "today") is
    server-side and stays there (#312's "a second opinion" rule).
    """

    recurrence: Recurrence
    due_date: date | None = None


class RecurrencePreviewRead(BaseModel):
    #: The next occurrence's due date. For after-completion mode this is what it *would* get if
    #: the carrier were finished today — never presented as a certainty by the editor.
    next_date: date
    #: A few more, so a rule that reads right for one date can be checked against its rhythm.
    following: list[date] = Field(default_factory=list)
    #: True for ``after_completion``: the next one appears when this one is finished, not on a day.
    on_completion: bool
    #: The first block the rule would book (#335 phase 5), when it carries a plan — kept for
    #: the callers that read one clock; ``blocks`` is the whole answer.
    planned_start: time | None = None
    planned_end: time | None = None
    #: Every block the next occurrence would book, each on the day its placement resolves to.
    blocks: list[PlannedBlockRead] = Field(default_factory=list)
    #: Schedule mode: how many occurrences the rule lays out inside the year ahead — the number of
    #: tasks saving it will create. ``None`` for after-completion, which creates one at a time.
    year_count: int | None = None


class PlannedBlockRead(BaseModel):
    """One block of the next occurrence, resolved: the placement turned into a date."""

    on: str
    day: date
    start_time: time
    end_time: time
    duration_minutes: int
    #: ``None`` means the occurrence's own roster, which the preview cannot know.
    user_ids: list[uuid.UUID] | None = None
    #: A day the rule cannot book: it falls before today, so a spawn today would skip it.
    in_past: bool = False


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

    @field_validator("title")
    @classmethod
    def _named(cls, value: str) -> str:
        """A task is named, and three spaces is not a name. ``min_length`` counts characters,
        so it let a whitespace title through as a real one; the stored title is the trimmed
        one, so the list never sorts on a leading space either."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("errors.required")
        return stripped
    # Nullable *here* because ``TaskRead`` inherits this shape and every instance that upgrades
    # into #392 carries rows written before the deadline was required — a read model that could
    # not express them would 500 on the tenant's own backlog. The **write** shapes are where the
    # rule lives: ``TaskCreate`` overrides this as required, and ``TaskUpdate`` refuses an
    # explicit ``null`` in the service. Expand/contract (docs/WORKFLOW.md): the column stays
    # nullable for at least a release, so an unattended ``alembic upgrade head`` on somebody
    # else's data cannot fail on data this release is the first to forbid.
    due_date: date | None = None
    allocated_minutes: int | None = Field(default=None, ge=0, le=100000)
    # Per-task close policy (#157 extended): when set, this task can only reach a finished
    # status once a designated closing contact moment is linked, regardless of the status flag.
    requires_interaction: bool = False
    # Client-portal visibility: off by default — staff opt a task in explicitly.
    visible_to_client: bool = False


class TaskCreate(TaskBase):
    #: **Required** (#392), narrowing ``TaskBase``'s nullable column type. A task with no
    #: deadline is absent from ``?due=overdue``, from the Agenda's deadline feed and from both
    #: dashboards' overdue counts — it is not merely unscheduled, it is invisible to the entire
    #: urgency vocabulary, which is what the team means by *niet kan worden overgeslagen*. So
    #: every creator states one, and the ones with nobody in front of them state a **default**
    #: rather than inheriting ``NULL``: the recurrence generator computes it from the rule, a
    #: template item from its ``relative_due_days`` (else the day it is applied), an automation
    #: rule from its ``due_days`` (else the day it fires), the import refuses the row by naming
    #: the column, and create-then-edit writes the org's own today over a placeholder row it is
    #: about to drop the user into.
    #:
    #: A **deadline is not a calendar booking**: ``Geplande blokken`` (#188/#335) stays optional,
    #: and setting one never implies the other.
    due_date: date
    #: The employees on this task, one starred as primary (#375). ``None`` means *the caller
    #: didn't say* — and ``assignee_user_id`` alone decides, which is the pre-roster shape every
    #: existing client (and the MCP surface generated from this spec) still posts. Never send a
    #: guess. **A task always has someone on it**: a create that names no employee and no
    #: client contact is handed to the project's responsible, else the client's, else the
    #: *caller* (``TaskService.create``) — so ``[]`` is not "assign nobody" but "I named nobody,
    #: resolve it", and the only create refused on this account is a portal login's, which
    #: cannot hold a task. Every screen asks for the roster explicitly and the update path
    #: refuses to empty it; the default here is for the callers with nobody in front of them.
    assignees: list[AssigneeWrite] | None = None
    recurrence: Recurrence | None = None
    #: **A task is always a client's.** ``company_id`` stays optional *on the wire* only so a
    #: caller may name the project alone and let the service take the client off it — a project
    #: has exactly one — and every other create that names no client is refused with the field
    #: named (``errors.tasks_company_required``, ``TaskService.create``). Not defaulted for the
    #: callers with nobody in front of them, the way the deadline is: a deadline has an honest
    #: default (today) and a client does not — the agency's own work is a client too, and only
    #: the caller knows which one. The column stays nullable for the rows written before this
    #: (expand/contract, docs/WORKFLOW.md), and ``TaskUpdate`` refuses clearing it.
    #:
    #: There is no ``unnamed`` here any more (#350): create-then-edit used to write a placeholder
    #: title and mark the row so a list could italicise it and a filter could gather it. That was
    #: a mitigation for a row nobody had asked for; the row is no longer written at all — every
    #: create names the task before it exists — so the flag has nothing left to say and a caller
    #: cannot set it. ``TaskRead.unnamed`` stays, read-only, for the rows an instance already has.
    #: What the task arrives *with* (#382): its steps, its links, its labels. All optional, and
    #: all written through this service's own ``add_checklist`` / ``add_link`` /
    #: ``set_task_labels`` inside the create's transaction — a composite create, never a second
    #: write path.
    #:
    #: They exist because a dictated task is **one** utterance, and posting it as 1 + 1 + N + M
    #: round trips is the shape docs/PERFORMANCE.md rejects on every other screen. They pay for
    #: themselves outside voice too: §12 makes every operation an MCP tool, so an agent can now
    #: create a task *with its steps* in one call instead of four.
    #:
    #: Forward-referenced because ``ChecklistItemCreate`` and ``LinkCreate`` are defined further
    #: down with the surfaces they belong to; ``model_rebuild()`` at the foot of this module is
    #: what resolves them. Moving those two up here would file them under "the create shape",
    #: which is not what they are.
    checklist: TaskCreateChecklist | None = None
    links: list[LinkCreate] = Field(default_factory=list, max_length=10)
    label_ids: list[uuid.UUID] = Field(default_factory=list, max_length=20)


class TaskLogTime(BaseModel):
    """"Ook de uren registreren" (#314): the hours the task took, written in the same
    transaction as the finish that offered to record them.

    The shape ``InteractionCreate.log_time`` already established (#175), plus the two things a
    task knows that a contact moment does not. ``schedule_id`` names an unlogged planned block
    (#188) this confirms, so the same hours can never be booked twice — through the finish
    prompt *and* again from the schedule panel. ``billable`` left out defers to the project
    (#284): a task on a subscription-covered project bills nobody, and a finish prompt that
    silently posted ``true`` would be the one write path that forgot.

    Times follow the *time* module's wall-clock-as-UTC convention, like every other entry.
    """

    started_at: datetime
    ended_at: datetime
    #: Blank falls back to the task's own title — a timesheet row reading "Homepage herzien"
    #: beats an empty one, and the task is the only thing the entry is about.
    description: str | None = Field(default=None, max_length=2000)
    billable: bool | None = None
    entry_type_key: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")
    schedule_id: uuid.UUID | None = None


class TaskUpdate(BaseModel):
    company_id: uuid.UUID | None = None
    project_id: uuid.UUID | None = None
    assignee_user_id: uuid.UUID | None = None
    # See ``TaskBase``: exclusive with ``assignee_user_id``, scoped to the task's company. The web
    # picker always posts both keys (one null) so switching kinds clears the other; a raw client
    # that sets this while leaving a live ``assignee_user_id`` gets the 422 exclusivity error.
    assignee_contact_id: uuid.UUID | None = None
    #: The roster (#375). Sending it replaces the whole thing. Sending only ``assignee_user_id``
    #: is a **hand-off** — it replaces the roster with that one person, which is what every
    #: pre-roster caller means by it and the one place a task differs from a client, where the
    #: same field merely moves the star. Adding somebody *beside* the assignee needs this field.
    #: Absent means neither, and nothing about the roster changes. A roster may be handed over
    #: and may **not** be emptied: ``[]`` with no ``assignee_contact_id`` (or a bare
    #: ``assignee_user_id: null``) is refused with the field named, the way an explicit ``null``
    #: deadline is (#392) — a task always has someone on it.
    assignees: list[AssigneeWrite] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    status: str | None = Field(default=None, max_length=50)
    priority: TaskPriority | None = None

    @field_validator("title")
    @classmethod
    def _named(cls, value: str | None) -> str | None:
        """Absent leaves the title alone; a whitespace one is refused, as on create."""
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            raise ValueError("errors.required")
        return stripped
    #: Absent leaves the deadline alone; an explicit ``null`` is **refused** (#392). CLAUDE.md
    #: §18's rule with its second half withdrawn: clearing is what stops being allowed, so a
    #: ``PATCH`` that mentions nothing but ``status`` still works on a task written before this
    #: — which is the acceptance criterion that matters most, since an agency's first act after
    #: upgrading must not be being unable to tick off its own backlog.
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
    # The hours this task took (#314), recorded with the finish rather than from memory a week
    # later. A *completion* ride-along, refused on any update that is not a move into a finished
    # status — never a general "create a time entry via PATCH" back door.
    log_time: TaskLogTime | None = None
    #: On a task that belongs to a schedule-mode series (the root, or one of its occurrences): does
    #: a change of **assignee** apply to this one, or to this one and every following occurrence
    #: (``future``)? The second hands the future over — the sibling rosters, the planned blocks
    #: already booked on the leaver's calendar, and the rule's own plan people — in one request.
    #: Only the assignee travels; every other field is this occurrence's own. Absent means
    #: ``this``, because the screen asks the question and an API caller that did not answer it
    #: meant the row it named. Nullable rather than defaulted so the generated client keeps it
    #: optional (``--default-non-nullable``).
    apply_to: Literal["this", "future"] | None = None


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    #: The employees on this task, primary first (#375). ``assignee_user_id`` above is the same
    #: person as the starred entry here — read this, and treat that as the compatibility mirror
    #: it is. Empty on a task assigned to a client contact, and on one assigned to nobody.
    assignees: list[AssigneeRead] = Field(default_factory=list)
    #: The client contact this task is assigned to, by name (#453) — resolved by the service so
    #: a reader who cannot list contacts (a portal login reading their own task) still prints
    #: the person. ``None`` when ``assignee_contact_id`` is.
    assignee_contact_name: str | None = None
    #: Nobody has typed a title for this task. The stored ``title`` is still a real string (a
    #: placeholder), so a surface that has not been taught about this reads exactly as before;
    #: one that has renders its own locale's word for *unnamed* (#350).
    unnamed: bool = False
    # Always present on a stored task (the create default has been resolved to a real key).
    status: str
    position: float
    completed_at: datetime | None
    closing_interaction_id: uuid.UUID | None = None
    recurrence: Recurrence | None
    # When the daily cron will materialize the next occurrence (schedule mode; ``None`` on an
    # after-completion rule and on a task that does not repeat). Stored since #62 and read by
    # nobody until #335 — which is why a rule could not be read back anywhere: the card had a
    # frequency and no date, so "↻ Maandelijks" was the whole answer to "when is the next one?".
    recurrence_next_run: date | None = None
    #: The root of the schedule-mode series this occurrence was generated from (``None`` on the
    #: root itself and on any task that is not a laid-out occurrence). What lets a screen say
    #: "onderdeel van een reeks" and the hand-off find its siblings.
    recurrence_source_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    # The hour budget's burn (#313). On a list row only when asked for (``?hours=true``) — a row
    # carries only what its screen draws (§9) — and on any surface only for a caller holding
    # ``time.entry.read``. **Absent, never zero**: "nobody may tell you" and "nothing logged yet"
    # are different answers, and a client-portal login (which holds ``tasks.task.read``) gets the
    # first one. ``remaining_minutes`` is unclamped, like ``remaining_hours``: over budget reads
    # negative, and is ``None`` when there is no allocation to remain of.
    logged_minutes: int | None = None
    remaining_minutes: int | None = None
    # "schakl is filling this in from the email it came from" (#327) — a
    # :class:`~app.modules.tasks.models.TaskAIStatus`, or ``None`` on a task no AI run ever
    # touched. It rides every task shape rather than the card alone because the state it
    # describes is short-lived and the *list* is where a user watches for it to finish.
    ai_status: str | None = None


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


class DashboardTaskGroup(BaseModel):
    """Compact open-task aggregate for the dashboard; no 200-row lookup payloads."""

    entity_type: str
    entity_id: uuid.UUID | None
    label: str | None
    # The client a *project* row belongs to. A project name alone is not a name: two clients each
    # having an "Algemeen" or a "Website" project drew two identical rows on the tile, and the
    # only way to tell them apart was to open one. Null on a company row (its own label is the
    # client) and on the unlinked bucket.
    company_id: uuid.UUID | None = None
    company_name: str | None = None
    count: int
    # The urgency partition (#398). A count says how *much* work a client is carrying and
    # nothing about whether any of it is late, so the tile that ranked on it put five
    # comfortable tasks above one that was due last Tuesday. These three are disjoint and each
    # is exactly what its ``?due=`` chip shows, so every figure opens the list it counted;
    # ``count`` stays, because "how much is there" is still a question, just not the first one.
    overdue: int
    due_today: int = 0
    due_week: int = 0


class DashboardTaskItem(BaseModel):
    """Only the fields rendered by the personal dashboard task tile."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    priority: TaskPriority
    due_date: date | None
    # Whose task it is — ``DashboardTaskGroup.company_name``'s rule one level down: a title alone
    # is not a task, and "Nieuwsbrief plannen" on four clients' work drew four identical rows.
    # Resolved through the project when the task hangs off one; null for the agency's own to-do
    # items, which belong to no client and must not be labelled as if they did.
    company_id: uuid.UUID | None = None
    company_name: str | None = None


class DashboardTaskGroups(BaseModel):
    """The tile's page **and** how many groups exist behind it (#407, #398).

    The tile used to render every group a GROUP BY produced — an agency running eighty live
    projects got eighty rows on their My Day. A page needs a size, and a size needs a number
    beside it or the reader cannot tell the whole answer from the first screen of one.

    ``total`` counts the **groups**, not the tasks — it is what "en nog 7" is drawn from — and
    it rides on the same grouped query as the rows, so saying what is not shown costs no second
    read. ``items`` rather than ``groups`` because every capped dashboard read answers the same
    shape (:class:`DashboardMineSummary`, the project budgets tile); one envelope the widgets
    share is what keeps a reader from having to remember which key this particular tile used.
    """

    items: list[DashboardTaskGroup]
    total: int


class DashboardMineSummary(BaseModel):
    """My open tasks: the page, and the bucket counts of the **whole** set (#407, #397).

    The widget partitions its rows into over tijd / vandaag / deze week / later and prints a
    count per bucket. Counted off a truncated page those numbers are wrong rather than partial —
    worse than silence, because they read as measured. So the buckets are counted in SQL over
    every open task assigned to the caller, and the rows below them are the page.

    Four counts rather than three since #397: ``upcoming`` was "everything that is not overdue
    and not today", which is the tile's whole complaint — the week and the rest were one number
    as well as one heading. The boundaries are the ``?due=`` filter's, so a heading and the list
    it opens count the same rows.
    """

    items: list[DashboardTaskItem]
    total: int
    overdue: int
    due_today: int
    due_week: int
    later: int


# --------------------------------------------------------------------------- #
# Checklists
# --------------------------------------------------------------------------- #
class ChecklistItemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    # Markdown source, rendered sanitized by the web (issue #66); optional per item.
    description: str | None = None


class TaskCreateChecklist(BaseModel):
    """A checklist a task is born with (#382).

    Its own shape rather than ``ChecklistCreate`` because the two answer different questions:
    that one may name a *template* to copy, which is a second lookup a create has no business
    doing, and this one carries its items inline, which that one cannot.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)
    items: list[ChecklistItemCreate] = Field(default_factory=list, max_length=100)


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


class ChecklistDuplicate(BaseModel):
    """Copy an existing checklist into the same task.

    The caller names the copy — the roles precedent (§15): a "(kopie)" suffix invented in the
    API would be user-facing text written in one language, in a column no catalog reaches.
    Omitted means the source's title verbatim, which is what an unattended caller (MCP, a
    script) gets and can rename afterwards.
    """

    title: str | None = Field(default=None, min_length=1, max_length=255)


class ChecklistRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    title: str
    description: str | None = None
    position: int
    items: list[ChecklistItemRead] = Field(default_factory=list)


class ChecklistOrder(BaseModel):
    """The task's checklists in their new order — the whole order, not one moved row.

    A board of tasks reorders by fractional ``position`` midpoints (docs/UX.md) because it is
    long and renumbering it is a large write. A checklist is neither: a handful of rows, so one
    renumbering statement is cheaper than the float column it would take to avoid it, and an id
    list cannot drift the way two clients trading midpoints can.

    Ids this task does not own are a 404. Ids it *does* own that the payload omits keep their
    relative order **after** the named ones, so a checklist added in another tab mid-drag is
    appended rather than 409-ing a save the user cannot repair.
    """

    checklist_ids: list[uuid.UUID] = Field(min_length=1, max_length=200)


class ChecklistItemOrder(BaseModel):
    """One checklist's items in their new order — same contract as ``ChecklistOrder``."""

    item_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class ChecklistOrderRead(BaseModel):
    """The resulting order, including rows the payload did not name (see ``ChecklistOrder``).

    Ids rather than whole records: a reorder changes exactly one field, and the caller that
    needs the rest already has them.
    """

    ids: list[uuid.UUID]


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
    #: The comment this one answers (#312); ``None`` opens a new thread. A reply *to a reply* is
    #: re-rooted onto its parent's thread rather than refused — threads are one level deep.
    parent_id: uuid.UUID | None = None


class CommentUpdate(BaseModel):
    """An edit changes the words, never the conversation they were said in — so no ``parent_id``
    (#312). Moving a message between threads rewrites what both threads said."""

    body: str = Field(min_length=1)


class CommentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: The comment this answers (#312), or ``None`` for a thread opener. The list stays flat and
    #: the client nests on this: every existing consumer (the excerpt, the ``#comment-<id>``
    #: deep link, the count aggregate) keeps working, and the response cap stays one number.
    parent_id: uuid.UUID | None = None
    author_user_id: uuid.UUID | None
    # The live account's name while it exists, else the snapshot taken when the comment was
    # written (issue #64). ``author_deleted`` says which — the UI marks a departed author
    # rather than dropping their name.
    author_name: str | None = None
    author_deleted: bool = False
    #: Set when the comment was written by someone signed in *as* the author (#296).
    impersonator_name: str | None = None
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
    #: Set when someone was signed in *as* the actor at the time (#296) — the line then reads
    #: "the client (via Jan)". ``None`` on every ordinary row.
    impersonator_name: str | None = None
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


class TaskAIStatusRead(BaseModel):
    """The polled shape behind the "schakl leest de e-mail" pill (#327) — one short string.

    Deliberately not ``TaskRead``: this is fetched on a timer while a run is in flight, and the
    whole point of giving it its own endpoint is that it does not drag the card with it.
    """

    model_config = ConfigDict(from_attributes=True)

    ai_status: str | None = None


class SeriesOccurrenceRead(BaseModel):
    """One laid-out occurrence of a schedule-mode series, as the card lists the ones ahead."""

    id: uuid.UUID
    due_date: date | None
    status: str
    is_terminal: bool = False


class TaskSeriesRead(BaseModel):
    """The series a task belongs to (schedule mode): its root, the rule, and what lies ahead.

    Answered on the root and on every occurrence alike, so whichever of the year's tasks is
    open, the reader sees the same rule and the same list of what is still to come — and a
    link to where the rule is edited, which is the root and nowhere else.
    """

    root_id: uuid.UUID
    root_title: str
    recurrence: Recurrence
    #: Occurrences due today or later that are not finished, soonest first — the first few.
    upcoming: list[SeriesOccurrenceRead] = Field(default_factory=list)
    #: How many that list stands for in total (it is capped), so a screen can say "12 taken".
    upcoming_total: int = 0


class TaskDetail(TaskRead):
    """The full "card": everything the task detail page renders."""

    labels: list[LabelRead] = Field(default_factory=list)
    #: The schedule-mode series this task is the root or an occurrence of; ``None`` otherwise.
    series: TaskSeriesRead | None = None
    checklists: list[ChecklistRead] = Field(default_factory=list)
    comments: list[CommentRead] = Field(default_factory=list)
    #: The conversation is longer than the cap and what is above is missing. A capped read that
    #: says nothing reads as "that is all of them" (CLAUDE.md §17, docs/PERFORMANCE.md), and the
    #: card had no way to tell a task with exactly 200 comments from one with nine hundred.
    #: Answered without a second query: the read asks for one row more than it keeps.
    comments_truncated: bool = False
    activities: list[ActivityRead] = Field(default_factory=list)
    links: list[LinkRead] = Field(default_factory=list)
    # ``logged_minutes``/``remaining_minutes`` are inherited from ``TaskRead``. The card always
    # asks for them — one row, one grouped query — but they stay gated on ``time.entry.read``,
    # so the same burn a client cannot see on the list is not handed to them on the card.


# --------------------------------------------------------------------------- #
# Changing a task in words (``tasks/assist.py``)
# --------------------------------------------------------------------------- #
class TaskReviseRequest(BaseModel):
    """One typed instruction against one existing task — "voeg een stap toe voor de DNS".

    The words are the caller's own, so nothing here narrows what they may say; what the answer
    may *do* is bounded on the way in (``assist.revision_from_call``).
    """

    instruction: str = Field(min_length=1, max_length=4000)
    override_budget: bool = False


class TaskReviseResult(BaseModel):
    """What the revision did, and the card as it now stands.

    The whole detail rides along because every caller redraws the task after this: the review
    slide-over adopts it, the card reloads. ``changed`` names the kinds of change that landed
    so a screen can say "steps added, deadline moved" in its own words; ``summary`` is the
    model's one sentence to the colleague.
    """

    task: TaskDetail
    summary: str | None = None
    changed: list[str] = Field(default_factory=list)
    #: The answer hit the token ceiling; what landed may be short of what was asked.
    truncated: bool = False


class TaskChecklistGenerateRequest(BaseModel):
    """Write this task's steps from its title and notes; ``instruction`` is an optional hint."""

    instruction: str | None = Field(default=None, max_length=2000)
    override_budget: bool = False


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


class ScheduleBatchCreate(BaseModel):
    """One block per person, sharing a day, a start and a length: "schedule the kick-off for the
    three of us". A block is personal — one row, one calendar, one Google event — so several
    people is several rows, and this is the single call that writes them together (§18's shape,
    without the per-row reporting: every person is judged before anything is written, and a
    refusal for one is a refusal for all — a half-planned meeting is not a plan).

    Its own body rather than a ``user_ids`` on ``ScheduleCreate``, because that route answers
    with *one* block and this one answers with the list; changing the shape of an answer under
    an existing caller (the generated MCP tool included) is how a client comes to read the first
    of three rows as the whole result.
    """

    task_id: uuid.UUID
    user_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
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


class BusyItemRead(BaseModel):
    """One stretch of a person's time that is already taken (``app/core/busy.py``).

    ``title``/``ref``/``href`` are present exactly when the caller may read the row behind it;
    otherwise the window stands alone — the free/busy answer, Google's own rule for a
    colleague's calendar.
    """

    user_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    source: str
    kind: str = "busy"
    all_day: bool = False
    tentative: bool = False
    title: str | None = None
    ref: str | None = None
    href: str | None = None


class BusyFeedRead(BaseModel):
    """What the scheduling dialog draws beside the block it is about to book.

    ``unavailable`` names the sources that could not answer: a calendar with a third missing
    looks exactly like a free afternoon, and a conflict check may never look complete when it is
    not (§17). ``sources`` is every provider that exists on this instance, so the legend can say
    which calendars were consulted rather than leaving the viewer to guess.
    """

    items: list[BusyItemRead]
    sources: list[str]
    unavailable: list[str] = Field(default_factory=list)


class ScheduleLogTime(BaseModel):
    """Confirm-to-log a passed block as a real time entry (#188). Everything defaults from the
    block; the user may adjust the worked minutes, break, description and billable flag before
    saving. ``minutes`` overrides the block's own duration when the actual work differed."""

    minutes: int | None = Field(default=None, ge=0, le=24 * 60)
    break_minutes: int = Field(default=0, ge=0, le=24 * 60)
    description: str | None = Field(default=None, max_length=2000)
    billable: bool = True
    entry_type_key: str | None = Field(None, min_length=1, max_length=50, pattern=r"^[a-z0-9_]+$")


# ``TaskCreate`` forward-references ``TaskCreateChecklist`` and ``LinkCreate``, both defined
# below it with the surfaces they belong to. Pydantic leaves such a model incomplete until the
# names resolve, and an incomplete model raises on its first validation — which would be the
# first ``POST /tasks`` in production and not the import here.
TaskCreate.model_rebuild()
