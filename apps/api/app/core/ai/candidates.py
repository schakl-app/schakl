"""Candidate prefetch for the time quick-add parse (#246).

The parse used to *discover* the tenant's names through serial `companies.find` →
`projects.find` → `tasks.find` tool calls: each one a full model round trip, and genuinely
sequential because a project lookup wants the company id only the previous call could supply.
Three round trips to learn three names the database can answer in one batch.

So we answer them here instead, before the model is called at all, and hand the model a
shortlist it only has to *choose* from. Two things follow from that:

- **The lookup is a read like any other.** Every query goes through the module's own
  :class:`TenantScopedRepository`, so RLS (Golden Rule 1) and the company-group horizon (§15)
  apply exactly as they do to the find tools — this is the same data by a cheaper route, never
  a wider one. Each candidate type is additionally gated on the same permission its find tool
  declares, so a caller who may not read projects is offered no project candidates.
- **A shortlist is grounding, not a filter.** The find tools stay on the request as a fallback,
  and an id the model returns is still checked against what it was actually shown
  (``features._checked_uuid``) — the shortlist widens that evidence set, it never relaxes it.

The last query is the one that earns its keep: what this user logged over the last 30 days.
Name matching is a bare ILIKE with no fuzzy fallback, so "Jansen" typed as "Jansn" matches
nothing — but the client you booked hours to on Tuesday is almost certainly the one you mean
today.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_

from app.core.members import staff_select
from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.modules.projects.models import Project
from app.modules.tasks.models import Task, TaskLabel
from app.modules.tasks.statuses import load_statuses
from app.modules.time.models import TimeEntry
from app.modules.time.service import TimeEntryTypeService

#: Caps. A shortlist is a hint, not a catalog: past these sizes the prompt grows faster than
#: the answer improves, and the fallback find tools are still there for the long tail.
_COMPANY_LIMIT = 15
_PROJECT_LIMIT = 20
_TASK_LIMIT = 20
_RECENT_DAYS = 30
_RECENT_LIMIT = 10
#: The roster is offered **whole** rather than searched (#382). An assignee is dictated by first
#: name and an ILIKE over the line's tokens would not find them — "Jan" against "Jan-Willem
#: Bakker" matches, "Willem" does not, and neither does a name the recogniser spelled its own
#: way. An agency is tens of people; this is the size at which "offer them all" stops holding.
_MEMBER_LIMIT = 60
#: Labels are a short tenant-authored vocabulary, so they go in the prompt for the same reason
#: the time parse already inlines the entry-type keys: cheaper than a tool call to fetch them.
_LABEL_LIMIT = 60

#: Which blocks a caller pays for. The time quick-add wants none of the task-only queries and
#: vice versa — three extra round trips on every parse is the docs/PERFORMANCE.md failure with
#: a model in front of it.
TIME_BLOCKS = frozenset({"companies", "projects", "tasks", "entry_types"})
TASK_BLOCKS = frozenset({"companies", "projects", "members", "labels", "statuses"})

#: Tokens that never name a record. Deliberately small and literal — this list only decides
#: what we *search for*, and a stray token costs one harmless ILIKE, while a missing one costs
#: a match. Dutch and English, since both are first-class input languages (§8).
_STOPWORDS = frozenset(
    {
        "aan", "and", "bij", "der", "een", "het", "for", "the", "van", "vandaag",
        "voor", "with", "met", "over", "naar", "uur", "uren", "hour", "hours", "min", "minuten",
        "minutes", "gisteren", "eergisteren", "morgen", "today", "yesterday", "tomorrow",
        "maandag", "dinsdag", "woensdag", "donderdag", "vrijdag", "zaterdag", "zondag",
        "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
        "afgelopen", "vorige", "last", "this", "deze", "was", "were", "heb", "have",
        "declarabel", "niet", "billable", "pauze", "break", "lunch",
    }
)

#: Spans that are time, not names — stripped before tokenising so "14:00-16:30" never becomes
#: a search term. Mirrors what the prompt teaches the model to read (prompts.time_parse_system).
_TIME_SPAN_RE = re.compile(
    r"""
    \d{1,2}[:.]\d{2}          # 14:00, 14.00
    | \d+[,.]?\d*\s*(?:u(?:ur|urs?)?|h(?:ours?|rs?)?|m(?:in(?:uten|utes?)?)?)\b
    | \d{1,2}-\d{1,2}(?!\d)   # 14-16
    | \d{1,2}[-/]\d{1,2}(?:[-/]\d{2,4})?   # 03-06, 03-06-2026
    """,
    re.IGNORECASE | re.VERBOSE,
)

_TOKEN_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def name_tokens(text: str) -> list[str]:
    """The words in one quick-add line that could plausibly name a record.

    Times and dates are removed first (they are read by the model, not looked up), then
    stopwords, then anything under three characters — a two-letter ILIKE matches most of the
    address book and tells us nothing.
    """
    stripped = _TIME_SPAN_RE.sub(" ", text)
    seen: dict[str, None] = {}
    for match in _TOKEN_RE.finditer(stripped):
        token = match.group(0).lower()
        if token not in _STOPWORDS:
            seen.setdefault(token, None)
    return list(seen)[:8]


@dataclass
class ParseCandidates:
    """What the tenant actually has that this line might be talking about."""

    companies: list[dict[str, str | None]] = field(default_factory=list)
    projects: list[dict[str, str | None]] = field(default_factory=list)
    tasks: list[dict[str, str | None]] = field(default_factory=list)
    entry_type_keys: set[str] = field(default_factory=set)
    #: Staff who could be given a task (#382). Whole roster, not a search — see ``_MEMBER_LIMIT``.
    members: list[dict[str, str | None]] = field(default_factory=list)
    labels: list[dict[str, str | None]] = field(default_factory=list)
    #: The org's own task-status keys. Slugs, not ids, so they are grounded by membership in this
    #: set (``features._checked_key``) rather than by the UUID evidence rule.
    status_keys: set[str] = field(default_factory=set)

    def member_ids(self) -> set[str]:
        """The staff ids that were shown — a set of its **own**, deliberately.

        ``ids()`` is one pool across companies, projects and tasks, which is harmless there:
        a project id offered as a company would fail the write. An assignee is different — a
        pool shared with three other entity types would let a confused answer put a *label's*
        id in ``assignee_user_id``, and the id space is the same. Grounding per type is what
        keeps a wrong answer null instead of plausible.
        """
        return {str(m["id"]).lower() for m in self.members if m.get("id")}

    def label_ids(self) -> set[str]:
        return {str(row["id"]).lower() for row in self.labels if row.get("id")}

    def ids(self) -> set[str]:
        """Every id the model was shown — the evidence set a returned id is checked against.

        Only the id-valued columns, never `name`/`title`. The set is unioned with
        ``features._seen_ids``, which reads ids out of tool results with a UUID regex, and
        this is what admits an id into the same trusted set — so it holds itself to the same
        standard rather than sweeping up whatever a row happens to contain.
        """
        found: set[str] = set()
        for row in (*self.companies, *self.projects, *self.tasks):
            for key, value in row.items():
                if value and (key == "id" or key.endswith("_id")):
                    found.add(value.lower())
        return found

    def as_prompt_block(self) -> str:
        """Compact `id<TAB>name` lines. Ids are copied verbatim by the model, so they are
        never abbreviated; names are what it matches against."""
        parts: list[str] = []
        if self.companies:
            rows = "\n".join(f"{c['id']}\t{c['name']}" for c in self.companies)
            parts.append(f"CLIENTS (id\tname):\n{rows}")
        if self.projects:
            rows = "\n".join(
                f"{p['id']}\t{p['name']}\tclient={p['company_id'] or '-'}" for p in self.projects
            )
            parts.append(f"PROJECTS (id\tname\tclient):\n{rows}")
        if self.tasks:
            rows = "\n".join(
                f"{t['id']}\t{t['title']}\tproject={t['project_id'] or '-'}" for t in self.tasks
            )
            parts.append(f"TASKS (id\ttitle\tproject):\n{rows}")
        if self.members:
            rows = "\n".join(f"{m['id']}\t{m['name']}" for m in self.members)
            parts.append(f"COLLEAGUES (id\tname):\n{rows}")
        if self.labels:
            rows = "\n".join(f"{row['id']}\t{row['name']}" for row in self.labels)
            parts.append(f"LABELS (id\tname):\n{rows}")
        if self.status_keys:
            parts.append("TASK STATUSES: " + ", ".join(sorted(self.status_keys)))
        if self.entry_type_keys:
            parts.append("ENTRY TYPES: " + ", ".join(sorted(self.entry_type_keys)))
        return "\n\n".join(parts)


def _company_row(company: Company) -> dict[str, str | None]:
    return {"id": str(company.id), "name": company.name}


def _project_row(project: Project) -> dict[str, str | None]:
    return {
        "id": str(project.id),
        "name": project.name,
        "company_id": str(project.company_id) if project.company_id else None,
    }


def _task_row(task: Task) -> dict[str, str | None]:
    return {
        "id": str(task.id),
        "title": task.title,
        "company_id": str(task.company_id) if task.company_id else None,
        "project_id": str(task.project_id) if task.project_id else None,
    }


async def _recent_ids(ctx: RequestContext) -> tuple[set[uuid.UUID], set[uuid.UUID], set[uuid.UUID]]:
    """What this user booked hours to lately — the answer an ILIKE on a typo cannot give.

    One grouped query, own rows only: a shortlist built from someone else's week would be
    noise, and `user_id` here is the caller, not a scope the model may widen.
    """
    since = datetime.now(UTC) - timedelta(days=_RECENT_DAYS)
    stmt = (
        ctx.repo(TimeEntry)
        .scoped_select()
        .with_only_columns(TimeEntry.company_id, TimeEntry.project_id, TimeEntry.task_id)
        .where(TimeEntry.user_id == ctx.user.id, TimeEntry.started_at >= since)
        .group_by(TimeEntry.company_id, TimeEntry.project_id, TimeEntry.task_id)
        .order_by(func.max(TimeEntry.started_at).desc())
        .limit(_RECENT_LIMIT)
    )
    rows = (await ctx.session.execute(stmt)).all()
    companies = {r[0] for r in rows if r[0]}
    projects = {r[1] for r in rows if r[1]}
    tasks = {r[2] for r in rows if r[2]}
    return companies, projects, tasks


async def gather(
    ctx: RequestContext, text: str, *, blocks: frozenset[str] = TIME_BLOCKS
) -> ParseCandidates:
    """Resolve the shortlist for one dictated or typed line.

    Each block is gated on the permission its find tool declares — same visibility rule, one
    implementation — and every read rides the tenant repository, so a restricted login gets a
    shorter list rather than a wider one.

    ``blocks`` is what keeps the two callers from paying for each other's queries: the time
    quick-add has no use for the roster and a dictated task has no use for entry types, and a
    shortlist that costs three unused round trips on every parse is a performance bug hiding
    behind a model call nobody times.
    """
    candidates = ParseCandidates()
    tokens = name_tokens(text)
    wants_recent = bool(blocks & {"companies", "projects", "tasks"})
    recent_companies, recent_projects, recent_tasks = (
        await _recent_ids(ctx)
        if wants_recent and ctx.can("time.entry.read")
        else (set(), set(), set())
    )

    if "companies" in blocks and ctx.can("companies.company.read"):
        stmt = ctx.repo(Company).scoped_select()
        # Both names and the klantnummer — the same fields the list and the MCP tool search
        # (``app/core/naming.py``), so somebody dictating "twee uur voor Jansen Holding" reaches
        # the same client the search box would have.
        matches = [
            or_(
                Company.name.ilike(f"%{t}%"),
                Company.legal_name.ilike(f"%{t}%"),
                Company.client_number.ilike(f"%{t}%"),
            )
            for t in tokens
        ]
        # An empty line still gets the recent set: "2 uur" on the client you always book to.
        criteria = [*matches, Company.id.in_(recent_companies)] if recent_companies else matches
        if criteria:
            rows = (
                (await ctx.session.execute(stmt.where(or_(*criteria)).limit(_COMPANY_LIMIT)))
                .scalars()
                .all()
            )
            candidates.companies = [_company_row(c) for c in rows]

    if "projects" in blocks and ctx.can("projects.project.read"):
        stmt = ctx.repo(Project).scoped_select()
        matches = [Project.name.ilike(f"%{t}%") for t in tokens]
        criteria = [*matches, Project.id.in_(recent_projects)] if recent_projects else matches
        if criteria:
            rows = (
                (await ctx.session.execute(stmt.where(or_(*criteria)).limit(_PROJECT_LIMIT)))
                .scalars()
                .all()
            )
            candidates.projects = [_project_row(p) for p in rows]

    if "tasks" in blocks and ctx.can("tasks.task.read"):
        stmt = ctx.repo(Task).scoped_select()
        matches = [Task.title.ilike(f"%{t}%") for t in tokens]
        criteria = [*matches, Task.id.in_(recent_tasks)] if recent_tasks else matches
        if criteria:
            rows = (
                (await ctx.session.execute(stmt.where(or_(*criteria)).limit(_TASK_LIMIT)))
                .scalars()
                .all()
            )
            candidates.tasks = [_task_row(t) for t in rows]

    # The entry-type vocabulary is a handful of short keys, so it belongs in the prompt rather
    # than behind a tool call the model would have to spend a round trip on.
    if "entry_types" in blocks and ctx.can("time.entry.read"):
        candidates.entry_type_keys = await TimeEntryTypeService(ctx).active_keys()

    # --- the task blocks (#382) ------------------------------------------------------ #
    if "members" in blocks:
        # `/members/lookup` declares no permission ("open to every member") and this is the
        # same answer by the same statement, so it carries no gate of its own either. What it
        # does carry is that endpoint's client-role exclusion, via the shared `staff_select`:
        # a portal contact holds a membership and is never an assignee.
        #
        # `active_only` rather than a local `User.is_active` filter: this shortlist is offered to
        # a model that is about to *assign* work, so "who still works here" has to be the whole
        # answer, and a colleague deactivated on their membership alone would otherwise come back
        # onto the list the day the column shipped.
        rows = (
            (
                await ctx.session.execute(
                    staff_select(ctx.org.id, active_only=True).limit(_MEMBER_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        candidates.members = [
            {"id": str(u.id), "name": u.full_name or u.email} for u in rows
        ]

    if "labels" in blocks and ctx.can("tasks.task.read"):
        rows = (
            (
                await ctx.session.execute(
                    ctx.repo(TaskLabel)
                    .scoped_select()
                    .order_by(TaskLabel.position, TaskLabel.name)
                    .limit(_LABEL_LIMIT)
                )
            )
            .scalars()
            .all()
        )
        candidates.labels = [{"id": str(row.id), "name": row.name} for row in rows]

    if "statuses" in blocks and ctx.can("tasks.task.read"):
        statuses = await load_statuses(ctx.session, ctx.org.id)
        candidates.status_keys = {s.key for s in statuses}
    return candidates


__all__ = ["TASK_BLOCKS", "TIME_BLOCKS", "ParseCandidates", "gather", "name_tokens"]
