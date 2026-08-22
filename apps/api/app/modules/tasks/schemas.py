"""Pydantic schemas for the tasks module (CLAUDE.md §6, §9)."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.tasks.models import (
    RecurrenceFreq,
    RecurrenceMode,
    TaskPriority,
    TemplateTrigger,
)
from app.schemas import AssigneeRead, AssigneeWrite


class RecurrencePlan(BaseModel):
    """"Herhaal ook de planning" (#335): the clock a spawned occurrence books itself at.

    The **day** comes from the occurrence — its due date, which the anchors below pin — so this
    carries only what the day cannot say: who, from when, for how long. ``user_id`` omitted means
    *the occurrence's own assignee*, resolved at spawn time rather than frozen here: a recurring
    task whose assignee moves to a colleague must plan the colleague's calendar, not the person
    who happened to write the rule.
    """

    user_id: uuid.UUID | None = None
    start_time: time
    duration_minutes: int = Field(ge=1, le=24 * 60)


class Recurrence(BaseModel):
    """A repeat rule, stored whole in ``tasks.recurrence`` (JSONB — no migration, #335).

    The anchors are **optional and absent by default**, which is what keeps every rule stored
    before #335 valid and unchanged: with none of them set, the cadence still hangs off the due
    date exactly as it did. Setting one pins the rhythm to a calendar the user can name — "elke
    maand op dag 1" rather than "a month after whatever the deadline happens to be".

    Which anchor a frequency accepts is a property of the frequency, so a mismatched pair is a
    422 rather than a field silently ignored: a rule that says "weekly on day 15" and quietly
    repeats every Tuesday is worse than one that refuses to save.
    """

    freq: RecurrenceFreq
    interval: int = Field(default=1, ge=1, le=365)
    mode: RecurrenceMode = RecurrenceMode.AFTER_COMPLETION
    #: Weekly only. ``date.weekday()`` numbering — Monday 0 … Sunday 6.
    on_weekday: int | None = Field(default=None, ge=0, le=6)
    #: Monthly/quarterly/yearly. Clamped to the month's length, so 31 lands on 28/29/30 Feb.
    on_day: int | None = Field(default=None, ge=1, le=31)
    #: Yearly only, and only together with ``on_day``.
    on_month: int | None = Field(default=None, ge=1, le=12)
    #: Book each occurrence onto a calendar as it is created (#335, phase 5).
    plan: RecurrencePlan | None = None

    @model_validator(mode="after")
    def _anchors_match_freq(self) -> Recurrence:
        allowed: dict[str, set[str]] = {
            RecurrenceFreq.DAILY.value: set(),
            RecurrenceFreq.WEEKLY.value: {"on_weekday"},
            RecurrenceFreq.MONTHLY.value: {"on_day"},
            RecurrenceFreq.QUARTERLY.value: {"on_day"},
            RecurrenceFreq.YEARLY.value: {"on_day", "on_month"},
        }[self.freq.value]
        for field in ("on_weekday", "on_day", "on_month"):
            if getattr(self, field) is not None and field not in allowed:
                raise ValueError(f"errors.tasks_recurrence_anchor:{field}")
        # A year needs both halves or neither: "on day 15" with no month is not a date, and a
        # month with no day would have to invent one.
        if self.freq is RecurrenceFreq.YEARLY and (self.on_day is None) != (self.on_month is None):
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
    #: The block the rule would book on ``next_date`` (#335 phase 5), when it carries a plan.
    planned_start: time | None = None
    planned_end: time | None = None


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
    #: existing client (and the MCP surface generated from this spec) still posts. ``[]`` is a
    #: different sentence: assign nobody. Never send a guess.
    assignees: list[AssigneeWrite] | None = None
    recurrence: Recurrence | None = None
    #: Create-then-edit (#230): this row exists so the user can be landed on its detail page in
    #: edit mode, and the title it carries is a placeholder nobody typed. Marks the row so a
    #: list can say so, in the *reader's* language, and so an abandoned one can be found (#350).
    #: A caller who supplies a real title never sets this. Nullable so that the generated
    #: client makes it optional: every existing caller creating a *named* task must keep
    #: compiling without saying so.
    unnamed: bool | None = None
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
    #: Absent means neither, and nothing about the roster changes.
    assignees: list[AssigneeWrite] | None = None
    title: str | None = Field(default=None, min_length=1, max_length=512)
    description: str | None = None
    status: str | None = Field(default=None, max_length=50)
    priority: TaskPriority | None = None
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


class TaskRead(TaskBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    org_id: uuid.UUID
    #: The employees on this task, primary first (#375). ``assignee_user_id`` above is the same
    #: person as the starred entry here — read this, and treat that as the compatibility mirror
    #: it is. Empty on a task assigned to a client contact, and on one assigned to nobody.
    assignees: list[AssigneeRead] = Field(default_factory=list)
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
    overdue: int


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


class TaskDetail(TaskRead):
    """The full "card": everything the task detail page renders."""

    labels: list[LabelRead] = Field(default_factory=list)
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


# ``TaskCreate`` forward-references ``TaskCreateChecklist`` and ``LinkCreate``, both defined
# below it with the surfaces they belong to. Pydantic leaves such a model incomplete until the
# names resolve, and an incomplete model raises on its first validation — which would be the
# first ``POST /tasks`` in production and not the import here.
TaskCreate.model_rebuild()
