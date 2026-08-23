"""System-actor task writes — the tasks module's published surface for automation (issue #27).

The request-facing ``TaskService`` authorizes against ``ctx.user`` and records that person in
the activity trail. An automation run has no person: the worker executes with a
``SystemContext`` (``user=None`` ⇒ the actor is the system, §16), and *authorization already
happened* when a permission-gated rule author saved the rule. These helpers are that path:
tenant-scoped writes (the context's session carries the RLS GUC), an activity line naming the
rule instead of a person, and the same bus events the interactive path emits — with the
caller's extra payload merged in, which is how the automation depth counter rides along.

Deliberately *not* here: recurrence hand-off on completion and due-date accountability —
request-side flows that reason about a human's intent. Everything a v1 rule action needs is.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select

from app.core.events import EmitContext, emit
from app.core.richtext import MENTION_RE, sanitize_markdown
from app.core.timezone import org_today
from app.core.urls import reject_dangerous_url
from app.errors import AppError
from app.modules.tasks.models import (
    Task,
    TaskActivity,
    TaskAIStatus,
    TaskAssignee,
    TaskChecklist,
    TaskChecklistItem,
    TaskComment,
    TaskLink,
    TaskPriority,
    TaskStatus,
)


async def mirror_primary_assignee(
    session: Any, org_id: uuid.UUID, task_id: uuid.UUID, user_id: uuid.UUID | None
) -> None:
    """Keep ``task_assignees`` in step with a direct write to ``tasks.assignee_user_id`` (#375).

    ``TaskService`` writes the links and derives the column from them. The write paths that do
    not go through it — automation, AI enrichment, e-mail-to-task, applying a task template —
    set the column straight onto the row, and a mirror with no link behind it is not a cosmetic
    gap: since the roster is what "mijn taken", the person filter and ``:own`` all read, such a
    task would be assigned to someone and invisible to them. So every one of those paths calls
    this, and the invariant "the column always has a matching primary link" holds for the whole
    module rather than for the half of it that happens to use the service.

    Replaces the roster rather than adding to it: these callers are stating the assignee, not
    joining one. ``None`` leaves the task unassigned.
    """
    await session.execute(
        delete(TaskAssignee).where(
            TaskAssignee.org_id == org_id, TaskAssignee.task_id == task_id
        )
    )
    await session.flush()  # clear the one-primary partial unique index before re-claiming it
    if user_id is not None:
        session.add(
            TaskAssignee(
                org_id=org_id, task_id=task_id, user_id=user_id, is_primary=True
            )
        )
    await session.flush()


async def _record(
    ctx: EmitContext, task_id: uuid.UUID, action: str, actor_name: str | None, payload: dict
) -> None:
    ctx.session.add(
        TaskActivity(
            org_id=ctx.org.id,
            task_id=task_id,
            actor_user_id=None,  # NULL actor = the system; the name says which automation.
            actor_name=actor_name,
            action=action,
            payload=payload,
        )
    )
    await ctx.session.flush()


async def _emit(
    ctx: EmitContext,
    event: str,
    task: Task,
    recipients: list[uuid.UUID],
    params: dict[str, Any] | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "task_id": task.id,
        "title": task.title,
        "_recipients": recipients,
    }
    payload.update(params or {})
    payload.update(extra_payload or {})
    await emit(event, ctx, payload)


async def _task_or_error(ctx: EmitContext, task_id: uuid.UUID) -> Task:
    task = await ctx.session.scalar(
        select(Task).where(Task.org_id == ctx.org.id, Task.id == task_id)
    )
    if task is None:
        raise AppError("not_found", "errors.not_found", status_code=404)
    return task


async def create_task_system(
    ctx: EmitContext,
    *,
    title: str,
    company_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
    assignee_user_id: uuid.UUID | None = None,
    description: str | None = None,
    priority: str = TaskPriority.NORMAL.value,
    due_date: date | None = None,
    actor_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> Task:
    """Create a task as the system — an automation rule firing, with nobody in front of it.

    ``due_date`` is the rule's own answer (``due_days`` on the action config, resolved by the
    caller). ``None`` falls back to **the org's today**, never to ``NULL``: a deadline is
    required (#392), and a 422 raised inside a worker is a task nobody ever sees. Resolved on
    the org's calendar rather than the container's clock (CLAUDE.md §8) — a rule that fires at
    01:00 in Amsterdam must not date its task yesterday.
    """
    if priority not in {p.value for p in TaskPriority}:
        raise AppError("validation", "errors.validation", status_code=422)
    if due_date is None:
        due_date = await org_today(ctx.session, ctx.org.id)
    max_position = float(
        await ctx.session.scalar(
            select(func.max(Task.position)).where(Task.org_id == ctx.org.id)
        )
        or 0.0
    )
    task = Task(
        org_id=ctx.org.id,
        title=title,
        description=description,
        company_id=company_id,
        project_id=project_id,
        assignee_user_id=assignee_user_id,
        status=TaskStatus.OPEN.value,
        priority=priority,
        due_date=due_date,
        position=max_position + 1024.0,
    )
    ctx.session.add(task)
    await ctx.session.flush()
    await mirror_primary_assignee(ctx.session, ctx.org.id, task.id, assignee_user_id)
    await _record(ctx, task.id, "created", actor_name, {})
    await _emit(
        ctx,
        "task.created",
        task,
        [],
        {
            "status": task.status,
            "company_id": task.company_id,
            "project_id": task.project_id,
        },
        extra_payload,
    )
    if task.assignee_user_id is not None:
        await _emit(ctx, "task.assigned", task, [task.assignee_user_id], None, extra_payload)
    return task


async def set_task_status_system(
    ctx: EmitContext,
    task_id: uuid.UUID,
    status: str,
    *,
    actor_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> Task:
    if status not in {s.value for s in TaskStatus}:
        raise AppError("validation", "errors.validation", status_code=422)
    task = await _task_or_error(ctx, task_id)
    old_status = task.status
    if old_status == status:
        return task
    task.status = status
    if status == TaskStatus.DONE.value:
        task.completed_at = datetime.now(UTC)
    elif old_status == TaskStatus.DONE.value:
        task.completed_at = None
    await ctx.session.flush()
    await _record(ctx, task.id, "status_changed", actor_name, {"from": old_status, "to": status})
    await _emit(
        ctx,
        "task.status_changed",
        task,
        [task.assignee_user_id] if task.assignee_user_id else [],
        {"from": old_status, "to": status},
        extra_payload,
    )
    return task


async def assign_task_system(
    ctx: EmitContext,
    task_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    actor_name: str | None = None,
    extra_payload: dict[str, Any] | None = None,
) -> Task:
    task = await _task_or_error(ctx, task_id)
    if task.assignee_user_id == user_id:
        return task
    task.assignee_user_id = user_id
    await ctx.session.flush()
    await mirror_primary_assignee(ctx.session, ctx.org.id, task.id, user_id)
    await _record(ctx, task.id, "updated", actor_name, {"changed": ["assignee_user_id"]})
    await _emit(ctx, "task.assigned", task, [user_id], None, extra_payload)
    return task


# --------------------------------------------------------------------------- #
# AI enrichment (#327) — what a model may write into a task, and the one way in
# --------------------------------------------------------------------------- #
#: Hard ceilings on a plan the model proposes. They are here rather than in the caller because
#: this is the surface a *second* automation would reach for tomorrow, and a cap that lives in
#: one caller's prompt is a cap the next caller does not have.
MAX_CHECKLIST_ITEMS = 20
MAX_LINKS = 10
#: A due date the model reads out of a sentence is a guess about a calendar, so it is bounded
#: rather than trusted: an email saying "before the year 2100" must not park a task off the end
#: of every deadline view, and one quoting an old thread must not backdate it into overdue.
DUE_DATE_PAST_DAYS = 3
DUE_DATE_FUTURE_DAYS = 730


async def caller_may_write_task(ctx, task_id: uuid.UUID) -> bool:  # noqa: ANN001
    """May *this caller* edit that task? — the tasks module answering for its own rows.

    A ride-along write carries the gates of the module it writes into, not of the route it rode
    in on (#314). An AI enrichment is requested on ``POST /interactions/{id}/approve``, which
    declares ``interactions.interaction.review`` and says nothing at all about tasks — so the
    task half is asked here, where the rule lives.

    And the rule is not a plain key: ``tasks.task.write:own`` means **assignee** (#12), which no
    caller outside this module has any business knowing. Published as a predicate for exactly
    that reason — the alternative is every borrower re-deriving the scope rule and one of them
    getting it wrong.
    """
    if ctx.can("tasks.task.write", scope="any"):
        return True
    if not ctx.can("tasks.task.write", scope="own"):
        return False
    user = getattr(ctx, "user", None)
    if user is None:
        return False
    # Any assignee, not the starred one (#375) — the same answer ``TaskService`` gives, and it
    # has to be the same answer or a second assignee's approve would 403 where their own PATCH
    # of the very same task succeeds.
    return bool(
        await ctx.session.scalar(
            select(TaskAssignee.id).where(
                TaskAssignee.org_id == ctx.org.id,
                TaskAssignee.task_id == task_id,
                TaskAssignee.user_id == user.id,
            )
        )
    )


async def set_ai_status_system(
    ctx: EmitContext,
    task_id: uuid.UUID,
    status: TaskAIStatus,
    *,
    only_if: tuple[TaskAIStatus, ...] | None = None,
) -> bool:
    """Move the task's "schakl is filling this in" state (#327).

    ``only_if`` is the compare-and-set the worker path needs: two deliveries of the same job, or
    a job racing the reaper, must not both decide they own the run. ``False`` means the row was
    not in one of those states and this caller should stand down.
    """
    task = await ctx.session.scalar(
        select(Task).where(Task.org_id == ctx.org.id, Task.id == task_id)
    )
    if task is None:
        return False
    if only_if is not None and task.ai_status not in {s.value for s in only_if}:
        return False
    task.ai_status = status.value
    task.ai_status_at = datetime.now(UTC)
    await ctx.session.flush()
    return True


class TaskEnrichment:
    """One model-proposed plan for a task — the whole vocabulary an AI run may write.

    Deliberately narrow, and the omissions are the design. Nothing here can move the task to
    another client or project, reassign it, change its status, or tick ``visible_to_client``:
    those are the fields where obeying a sentence in an untrusted email would do real damage
    (exposing an internal task to a client portal, or filing a mail's work under the wrong
    company). The model's whole output channel is these six fields, so the blast radius of a
    prompt injection is bounded by construction rather than by the prompt asking nicely.
    """

    __slots__ = (
        "checklist_items",
        "checklist_title",
        "comment",
        "description",
        "due_date",
        "links",
        "requires_interaction",
    )

    def __init__(
        self,
        *,
        description: str | None = None,
        due_date: date | None = None,
        requires_interaction: bool | None = None,
        checklist_title: str | None = None,
        checklist_items: list[tuple[str, str | None]] | None = None,
        comment: str | None = None,
        links: list[tuple[str, str | None]] | None = None,
    ) -> None:
        self.description = description
        self.due_date = due_date
        self.requires_interaction = requires_interaction
        self.checklist_title = checklist_title
        self.checklist_items = checklist_items or []
        self.comment = comment
        self.links = links or []

    def empty(self) -> bool:
        return not any(
            (
                self.description,
                self.due_date,
                self.requires_interaction is not None,
                self.checklist_items,
                self.comment,
                self.links,
            )
        )


def _mention_label(match: re.Match[str]) -> str:
    """The display half of a mention marker — ``@[Jan](mention:…)`` becomes ``Jan``."""
    raw = match.group(0)
    start, end = raw.find("["), raw.find("]")
    return raw[start + 1 : end] if -1 < start < end else ""


def _untrusted_markdown(value: str | None, *, limit: int) -> str | None:
    """Model-written prose on its way into a stored record.

    Two passes, and the second is the one that is easy to forget. ``sanitize_markdown`` strips
    raw HTML, which is the stored-XSS half. The other half is *our own* markup: a mention marker
    (``@[Name](mention:<uuid>)``) is extracted on write and fans out a notification, and a task
    reference deep-links the board — so text derived from an outside party's email is exactly
    the text that must not be able to carry one. An email that quotes the syntax gets it read as
    the plain words it is.

    Stripped with ``MENTION_RE`` itself rather than a lookalike pattern, deliberately: whatever
    the extractors can *find* is what this has to be able to remove, and two regexes drifting
    apart is how the marker that still notifies gets through.
    """
    text_value = sanitize_markdown(value)
    if not text_value:
        return None
    text_value = MENTION_RE.sub(_mention_label, text_value).strip()
    return text_value[:limit] or None


async def apply_ai_enrichment_system(
    ctx: EmitContext, task_id: uuid.UUID, plan: TaskEnrichment, *, today: date
) -> dict[str, Any]:
    """Write a model's plan onto a task as the system, and report what actually landed.

    Every write is the automation shape (§16): a ``NULL`` actor, so the trail says the system
    did it and no ``users`` row is invented for a worker that has no person. That is also why
    this cannot go through ``TaskService`` — its ``_record`` stores ``ctx.user.id``, and a
    ``SystemContext``'s placeholder user exists in no table, so the FK would refuse the write
    that the whole run exists to make.

    **A description is appended, never replaced.** The task may already carry words a person
    wrote — the inline-create offers no description field, but this same tick works on a task
    that already existed — and overwriting them with model prose is the one irreversible thing
    in here.

    **The comment notifies nobody.** ``TaskService.add_comment`` fans out to the task's audience;
    a note the system left while reading an email is not somebody addressing the team, and a
    mailbox full of "schakl commented" is how a helpful feature gets turned off.

    ``today`` is passed in, never read from the clock here (§8): the due-date window is a
    statement about the org's own calendar, and a module that resolves its own "today" hands
    every tenant UTC's.
    """
    task = await _task_or_error(ctx, task_id)
    applied: dict[str, Any] = {}

    description = _untrusted_markdown(plan.description, limit=20_000)
    if description:
        existing = (task.description or "").strip()
        task.description = f"{existing}\n\n---\n\n{description}" if existing else description
        applied["description"] = True

    if plan.due_date is not None and task.due_date is None:
        # Only fills a blank: a deadline a person set is a commitment, and a sentence in an
        # email is not the thing that gets to move it. Since #392 the create surfaces all ask
        # for one, so the blank this fills is a row written before that release — deliberately
        # kept rather than widened, because "the model may move a date somebody chose" is a
        # different and much worse rule, and the input here is written by an outsider.
        if (
            today - timedelta(days=DUE_DATE_PAST_DAYS)
            <= plan.due_date
            <= today + timedelta(days=DUE_DATE_FUTURE_DAYS)
        ):
            task.due_date = plan.due_date
            applied["due_date"] = plan.due_date.isoformat()

    if plan.requires_interaction:
        # One-way on purpose. Turning the flag *on* adds a guard ("this cannot be closed
        # without answering the client"), which is safe to be wrong about; turning it off
        # would remove one a person asked for, on the say-so of the email it guards against.
        task.requires_interaction = True
        applied["requires_interaction"] = True

    if plan.checklist_items:
        title = _untrusted_markdown(plan.checklist_title, limit=255) or None
        checklist = TaskChecklist(
            org_id=ctx.org.id,
            task_id=task_id,
            title=title or _fallback_checklist_title(task),
            position=await _next_checklist_position(ctx, task_id),
        )
        ctx.session.add(checklist)
        await ctx.session.flush()
        count = 0
        for index, (item_title, item_description) in enumerate(
            plan.checklist_items[:MAX_CHECKLIST_ITEMS]
        ):
            clean = _untrusted_markdown(item_title, limit=512)
            if not clean:
                continue
            ctx.session.add(
                TaskChecklistItem(
                    org_id=ctx.org.id,
                    checklist_id=checklist.id,
                    title=clean,
                    description=_untrusted_markdown(item_description, limit=2000),
                    position=index,
                )
            )
            count += 1
        await ctx.session.flush()
        applied["checklist_items"] = count

    if plan.links:
        stored = 0
        for url, title in plan.links[:MAX_LINKS]:
            safe = _safe_url(url)
            if safe is None:
                continue
            ctx.session.add(
                TaskLink(
                    org_id=ctx.org.id,
                    task_id=task_id,
                    url=safe,
                    title=_untrusted_markdown(title, limit=255),
                )
            )
            stored += 1
        await ctx.session.flush()
        if stored:
            applied["links"] = stored

    comment = _untrusted_markdown(plan.comment, limit=4000)
    if comment:
        ctx.session.add(
            TaskComment(
                org_id=ctx.org.id,
                task_id=task_id,
                author_user_id=None,  # the system wrote it; ``author_name`` says which run
                author_name=None,
                body=comment,
                mentioned_user_ids=[],
                mentioned_contact_ids=[],
                mentioned_task_ids=[],
            )
        )
        await ctx.session.flush()
        applied["comment"] = True

    await ctx.session.flush()
    return applied


def _fallback_checklist_title(task: Task) -> str:
    """A checklist needs a title and the model may not have given one.

    The task's own title is the only thing here that is neither invented English nor untrusted
    email text — an i18n key cannot help, because this is stored tenant data written by a worker
    that has no reader and therefore no locale.
    """
    return task.title[:255]


def _safe_url(url: str | None) -> str | None:
    """A link the model proposed, or ``None`` to drop it.

    Dropped rather than raised: ``reject_dangerous_url`` answers a *user* filling in a form,
    where a 422 naming the field is the helpful thing. Here the whole run would die over one bad
    row in a list nobody typed, so a link we will not store simply is not stored.
    """
    if not url or not isinstance(url, str):
        return None
    candidate = url.strip()[:1024]
    if not candidate:
        return None
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        reject_dangerous_url(candidate, field="url")
    except AppError:
        return None
    return candidate


async def _next_checklist_position(ctx: EmitContext, task_id: uuid.UUID) -> int:
    return int(
        await ctx.session.scalar(
            select(func.count())
            .select_from(TaskChecklist)
            .where(TaskChecklist.org_id == ctx.org.id, TaskChecklist.task_id == task_id)
        )
        or 0
    )


async def record_ai_activity_system(
    ctx: EmitContext, task_id: uuid.UUID, action: str, payload: dict[str, Any]
) -> None:
    """One line on the task's own trail for what the AI run did (§16).

    ``actor_name`` stays ``None`` — genuinely the system, which is the trail's own contract for
    "no person did this". The *action* carries the meaning, and it resolves through
    ``tasks.activity.*`` like every other line, so the sentence a user reads is translated
    rather than an English string frozen into a database column (Golden Rule 2).
    """
    await _record(ctx, task_id, action, None, payload)
