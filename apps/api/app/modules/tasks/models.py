"""``Task`` and its satellites — labels, checklists, comments, activity, templates
(CLAUDE.md §6, §10).

Org-scoped throughout. A task attaches to a company (its client overview) and/or a project
(its to-do list), and is assignable to an org member. Status/priority are small closed
vocabularies kept as strings. ``position`` is a float so the web can reorder by fractional
midpoints without renumbering. Recurrence is deliberately simple: a JSONB blob
``{freq, interval, mode}`` carried by exactly one task per chain, plus a real
``recurrence_next_run`` date column so the daily cron can query it.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    column,
    select,
    table,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.mixins import OrgScopedMixin, TimestampMixin, UUIDPrimaryKeyMixin
from app.db import Base

#: The projects table as three columns, for the correlated EXISTS in
#: ``Task.__portal_horizon_clause__``. A name, not an import: this module already carries the FK
#: to ``projects.id`` the same way, and importing the projects module's model to build one
#: predicate is precisely the coupling CLAUDE.md §6 forbids. Nothing here reads a project's
#: *contents* — only which client it belongs to, which is the FK this table already declares.
_projects = table("projects", column("id"), column("org_id"), column("company_id"))


class TaskStatus(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class TaskPriority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TaskAIStatus(StrEnum):
    """How far the "let schakl fill this in" run has got (#327).

    A task created while approving an email can be populated from that email by the model. The
    body is not there yet when the task is created (the gmail fetch is asynchronous and
    deliberately outside the approving request's transaction), so the work is a worker job and
    this column is what the card shows meanwhile — the whole point being that nobody waits for
    it.

    ``SKIPPED`` is not a failure: it is "we looked and there was nothing to carry over" — a body
    that never landed, or a model that found no plan in it. Distinguishing it from ``FAILED``
    is what lets the card say something true rather than showing a red mark over an empty mail.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    SKIPPED = "skipped"
    FAILED = "failed"


class RecurrenceFreq(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RecurrenceMode(StrEnum):
    # Spawn the next occurrence when this one is completed.
    AFTER_COMPLETION = "after_completion"
    # Spawn on schedule (daily cron), regardless of completion.
    SCHEDULE = "schedule"


class TemplateTrigger(StrEnum):
    MANUAL = "manual"
    COMPANY_STATUS = "company_status"


class Task(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        # Dashboard/task-board paths always tenant-scope before narrowing the workflow state.
        Index("ix_tasks_org_status", "org_id", "status"),
        # My Day: one employee's unfinished work ordered/partitioned around its deadline.
        Index(
            "ix_tasks_org_assignee_status_due",
            "org_id",
            "assignee_user_id",
            "status",
            "due_date",
        ),
        # The company hub and the project detail each ask for one parent's unfinished work
        # (#290). ``(org_id, status)`` above cannot serve either: status is the *second*
        # column, so narrowing by parent first has no prefix to ride.
        Index("ix_tasks_org_company_status", "org_id", "company_id", "status"),
        Index("ix_tasks_org_project_status", "org_id", "project_id", "status"),
        # Deadline windows that are not one person's: "due this week" across the tenant, and
        # the reminder cron's `due_date <= horizon`. The assignee index below starts with a
        # user id, so an org-wide deadline scan cannot use it.
        Index("ix_tasks_org_due", "org_id", "due_date"),
        # Partial index: the daily cron only ever scans carriers with a pending next_run.
        Index(
            "ix_tasks_recurrence_next_run",
            "recurrence_next_run",
            postgresql_where=text("recurrence_next_run IS NOT NULL"),
        ),
        # Same shape for the enrichment reaper (#327): it only ever scans the handful of rows a
        # worker currently claims, never the whole table.
        Index(
            "ix_tasks_ai_status_running",
            "org_id",
            "ai_status_at",
            postgresql_where=text("ai_status IN ('queued', 'running')"),
        ),
    )

    # Client-portal visibility (#212 follow-up): a task is invisible to portal logins unless
    # staff explicitly tick it. Enforced in TaskService via a portal-filtered repository,
    # so a portal request can never reach an unticked task by any path (get, list, comments).
    visible_to_client: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # A task may belong to a project (a project's to-do list); SET NULL keeps the task if the
    # project is deleted. Cross-module FK by table name only — no import of the projects module.
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("projects.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    # A task may instead be assigned to a **contact of its own client company** (#273) — "waiting
    # on the client to send the materials". Mutually exclusive with ``assignee_user_id`` and
    # scoped to ``company_id`` in the service (a check constraint can't express the company link).
    # SET NULL like the employee assignee: deleting the contact unassigns, never deletes the task.
    # Cross-module FK by table name only — no import of the contacts module.
    assignee_contact_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("contacts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # A tenant-configured status key (issue #62), not a closed enum. Wide enough for a custom
    # slug; the ``TaskStatus`` default keeps a fresh row valid before the service sets it.
    status: Mapped[str] = mapped_column(
        String(50), nullable=False, default=TaskStatus.OPEN.value, index=True
    )
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskPriority.NORMAL.value
    )
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True, index=True)
    # Time budget for this task, in minutes (shown against logged time on the card).
    allocated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    position: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The contact moment this task was closed with (#157) — GitHub's "close with comment",
    # but a contactmoment. Deliberately no DB FK: interactions.task_id already points here
    # and a mutual FK is a circular dependency; the service validates linkage on write, and
    # reopening the task clears the designation so the next close picks afresh.
    closing_interaction_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), nullable=True
    )
    # Per-task policy (#157 extended): this task may only reach a finished (``is_terminal``)
    # status with a designated closing contact moment — independent of the per-status flag, so a
    # single task can demand it without the whole status doing so. Carried to the next occurrence
    # on recurrence and copied from a template item at apply time.
    requires_interaction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    recurrence: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    recurrence_next_run: Mapped[date | None] = mapped_column(Date, nullable=True)
    # "schakl is filling this in from the email" (#327). ``NULL`` on an ordinary task; every
    # other value is a :class:`TaskAIStatus`. Written only through ``tasks.system`` — the run
    # happens in a worker, where the actor is the system and ``TaskActivity.actor_user_id``'s
    # FK would refuse the placeholder user a ``SystemContext`` carries.
    ai_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # When the status last moved. The reaper reads it (a run whose worker died leaves
    # ``running`` behind and no process is left to say so — the reporting lesson, #300), and the
    # card stops polling on it.
    ai_status_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @classmethod
    def __portal_horizon_clause__(cls, scope: frozenset[uuid.UUID] | None):  # noqa: ANN206
        """The stricter rule an **external (client) login** reads tasks by (#266, §15).

        Two narrowings, and the visibility tick is only the second one. The first is the
        horizon, and it is *not* the one the repository would build itself: ``company_id`` is
        nullable, so the column-matched rule exempts a NULL — rows attached to no client are
        not company data (§15). That exemption is right for staff and exactly wrong here. An
        agency's own to-do list carries no ``company_id``, so a task ticked visible while
        unattached was visible to **every** client of the tenant rather than to none of them.

        But "has no ``company_id``" is not the same as "belongs to no client" — §285's failure
        mode (1), the missing anchor. A task on a project inherits its client from the
        *project*, and nothing fills the column in when one is created that way (a template, an
        import, the project page with its company field left empty). Dropping every NULL would
        therefore have swapped one bug for its mirror image: the client who could see somebody
        else's task would stop seeing their own. So the anchor is the column **when it has
        one** and the project's client only when it does not — the direct link stays
        authoritative, and a task explicitly filed under client A is never revealed by a
        project belonging to client B.

        ``projects`` is named as a table and never imported (§6) — the shape
        ``app/core/parent.py`` already uses for the reverse direction of this same FK.

        The second narrowing is what the checkbox is for: ``visible_to_client`` is the staff
        decision that this task is part of the conversation, and an unticked one is absent
        rather than forbidden.

        It lives on the model, like ``Invoice.__portal_horizon_clause__``, so every path gives
        the client the same answer *by construction*: the list and its **total**, the detail,
        the company panel and its open count, the comment target, and the two reference seams
        (``entity_visible`` — which is the only gate on ``GET /files`` — and
        ``app/core/directory.py``). Stating it once is what stops the #285 shape where one
        caller remembers the rule and the next one does not.
        """
        companies = scope or frozenset()
        via_project = (
            select(_projects.c.id)
            .where(
                _projects.c.id == cls.project_id,
                _projects.c.org_id == cls.org_id,
                _projects.c.company_id.in_(companies),
            )
            .exists()
        )
        anchored = cls.company_id.in_(companies) | (cls.company_id.is_(None) & via_project)
        return anchored & cls.visible_to_client.is_(True)


class TaskSchedule(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """A planned time block for a task on someone's calendar (#188).

    Distinct from ``Task.due_date`` (a deadline) and ``allocated_minutes`` (a budget): a schedule
    is *when someone intends to work on the task*. A task may carry **several** blocks — a six-hour
    task planned as two three-hour sessions — so this is its own row, never a column on ``tasks``.

    ``user_id`` is the person the block is for (defaults to the task's assignee, adjustable by a
    holder of ``tasks.schedule.write:any``). Instants are ``TIMESTAMPTZ``/UTC; the web renders them
    in the org timezone, and the Google push words them in the org's local time (like leave, §8).

    ``time_entry_id`` links a block to the time entry it was logged as (#48-style one-click log):
    a passed block offers "Registreer uren" which creates a real ``TimeEntry`` and stamps it here,
    so the block reads *logged* and is never counted twice. ``SET NULL`` re-opens the block if the
    entry is later deleted. The FK is cross-module by table name only — no import of ``time``.
    """

    __tablename__ = "task_schedules"
    __table_args__ = (
        Index("ix_task_schedules_org_user_start", "org_id", "user_id", "starts_at"),
        Index("ix_task_schedules_org_task", "org_id", "task_id"),
    )

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # SET NULL: an orphaned block (its person removed) is a scheduling error to clean up, not a
    # reason to lose the plan; the service treats a NULL user as unschedulable and hides it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # The time entry this block was logged as (#188). Cross-module FK by table name; SET NULL so
    # deleting the entry re-opens the block instead of stranding a dangling id.
    time_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("time_entries.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Who scheduled it, snapshotted (issue #64) so a departed scheduler still reads as a person,
    # not "System", in the notification and any future trail.
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_by_name: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TaskLabel(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_labels"
    __table_args__ = (UniqueConstraint("org_id", "name"),)

    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TaskStatusDef(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Tenant-configurable task status vocabulary (issue #62), modelled on ``TaskLabel``.

    Board grouping, sort order and "is this finished?" all read from this ordered list instead of
    a hardcoded ``TaskStatus`` enum. ``Task.status`` stores a status ``key``; ``key`` is an
    immutable slug. ``is_terminal`` replaces string-matching ``"done"`` for ``completed_at`` and
    recurrence-on-completion; ``is_default`` is the status a new task starts in. Seeded per org
    with ``open`` / ``in_progress`` / ``done`` so nothing regresses (see ``statuses.py``).
    """

    __tablename__ = "task_statuses"
    __table_args__ = (UniqueConstraint("org_id", "key"),)

    key: Mapped[str] = mapped_column(String(50), nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str] = mapped_column(String(20), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # A finished state: stamping ``completed_at`` and spawning an after-completion recurrence key
    # off this flag, never the literal string "done" (issue #62).
    is_terminal: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Entering this status demands a designated closing contact moment (#157) — tenant
    # policy per status, so "klaar" can be made to mean "besproken met de klant".
    requires_interaction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # The status a newly created task falls into when none is given.
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )


class TaskLabelLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_label_links"
    __table_args__ = (UniqueConstraint("task_id", "label_id"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    label_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_labels.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )


class TaskChecklist(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_checklists"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Markdown source, rendered sanitized by the web (issue #66). Nullable — a checklist need not
    # explain itself; the title usually carries the whole meaning.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TaskChecklistItem(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_checklist_items"

    checklist_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_checklists.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    # Markdown source (issue #66): the "how" behind a one-line "what". Nullable by design.
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    done: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class TaskChecklistTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Org-wide reusable checklist attachable to any task card.

    Items reshaped from bare titles to ``{title, description}`` for issue #66. The reshape is a
    type change on a populated JSONB column, so it ships expand/contract (docs/WORKFLOW.md):
    ``items_rich`` is the new object shape, backfilled and written on every save, while ``items``
    (title-only) stays dual-written so a rolled-back previous image still reads the checklist it
    expects. ``items`` is dropped in the contract release once N is adopted.
    """

    __tablename__ = "task_checklist_templates"

    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Legacy title-only shape — kept for rollback safety only; new code reads ``items_rich``.
    items: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Authoritative shape: a list of ``{"title": str, "description": str | None}``.
    items_rich: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )


class TaskLink(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """URL attachment on a task (briefs, docs, designs). File uploads need object storage
    and are deliberately not modelled yet."""

    __tablename__ = "task_links"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    url: Mapped[str] = mapped_column(String(1024), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)


class TaskComment(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_comments"

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The comment this one answers (#312) — NULL for a comment that opens a thread. **One level
    # deep, and that is a product rule, not a schema shortcut**: a reply to a reply indents itself
    # off the screen and gives two readers two different reading orders, so the service re-roots it
    # onto the same parent rather than refusing it. CASCADE because a thread is one conversation:
    # the answers to a question that is gone are not a record of anything, and the confirm counts
    # them out loud before it happens (docs/UX.md).
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_comments.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # SET NULL so the thread survives a user's removal.
    author_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # The author's display name at the time of writing (issue #64). The FK is SET NULL, so a
    # live join is the one thing that cannot survive the account it joins to: it hands back
    # ``None`` and the thread reads "—", as if nobody ever wrote the words. Snapshotting the
    # name is what keeps the comment attributable. The live join still wins while the account
    # exists — a rename should show through — so this is a fallback, not the display value.
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Written *through* the author's account by someone signed in as them (#296). A comment is
    # the most visible thing an impersonated session produces — a client portal login's whole
    # write surface — so the bubble names both, or the agency's own words sit under the client's
    # name with nothing to say otherwise. Snapshotted like the author's, for the same reason.
    impersonator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    impersonator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    # Users @mentioned in the body (issue #63), captured structurally rather than re-parsed on every
    # render. Extracted from the `@[Name](mention:<uuid>)` markers by the service and validated
    # against org membership, so a mention notifies even a non-assignee who never commented before.
    mentioned_user_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Contacts @mentioned (#165) — parallel to, never folded into, the user list: contacts are
    # references into the CRM, not notification recipients, so the fan-out stays unambiguous.
    mentioned_contact_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Tasks #referenced (#197) — cross-links, validated org-scoped like the other kinds. Stored
    # structurally so a "referenced in" backlink can be built later without re-parsing bodies.
    mentioned_task_ids: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class TaskActivity(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    """Append-only audit trail; ``action`` maps to a ``tasks.activity.*`` i18n key."""

    __tablename__ = "task_activities"
    __table_args__ = (Index("ix_task_activities_task_id_created_at", "task_id", "created_at"),)

    task_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("tasks.id", ondelete="CASCADE"),
        nullable=False,
    )
    # NULL actor = the system (recurrence cron, automation).
    #
    # …which is exactly why ``actor_name`` exists (issue #64). The FK is SET NULL, so deleting a
    # user rewrites their history into the system's: "Jane closed this" becomes "System closed
    # this", and no query can tell the two apart afterwards. The snapshot disambiguates them —
    # a name with no ``actor_user_id`` is a departed human, no name at all is genuinely the
    # system. Written on every ``_record``; the live join still wins while the account exists.
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Who was really at the keyboard, when that is not the actor (#296) — the same pair core's
    # ``activity_log`` carries. A task is where a client portal login actually *writes* (its
    # comments, its checklist ticks), so it is the trail most likely to record an impersonated
    # act, and the one place the omission would be most misleading.
    impersonator_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    impersonator_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default="{}"
    )


class TaskTemplate(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_templates"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TemplateTrigger.MANUAL.value
    )
    # Company status that auto-applies this template (when trigger == company_status).
    trigger_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class TaskTemplateItem(UUIDPrimaryKeyMixin, OrgScopedMixin, TimestampMixin, Base):
    __tablename__ = "task_template_items"

    template_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("task_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default=TaskPriority.NORMAL.value
    )
    # Due date of the instantiated task = application date + this many days.
    relative_due_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    allocated_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    assignee_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Assign the instantiated task to the company's *primary responsible* (#28), resolved at
    # apply time — an onboarding task follows whoever owns the client, not a person fixed when
    # the template was written. Falls back to ``assignee_user_id``, then unassigned.
    assign_responsible: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    # Instantiated tasks may only be closed with a designated contact moment (#157 extended);
    # copied onto ``Task.requires_interaction`` at apply time.
    requires_interaction: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    checklist_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Legacy title-only shape — kept for rollback only; new code reads ``checklist_items_rich``.
    checklist_items: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    # Authoritative shape (issue #66): ``{"title": str, "description": str | None}`` per item,
    # which becomes a checklist — items and their descriptions — on the instantiated task. Reshaped
    # expand/contract alongside ``TaskChecklistTemplate.items`` (see that model's note).
    checklist_items_rich: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
