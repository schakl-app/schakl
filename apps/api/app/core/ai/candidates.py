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

from app.core.tenancy import RequestContext
from app.modules.companies.models import Company
from app.modules.projects.models import Project
from app.modules.tasks.models import Task
from app.modules.time.models import TimeEntry
from app.modules.time.service import TimeEntryTypeService

#: Caps. A shortlist is a hint, not a catalog: past these sizes the prompt grows faster than
#: the answer improves, and the fallback find tools are still there for the long tail.
_COMPANY_LIMIT = 15
_PROJECT_LIMIT = 20
_TASK_LIMIT = 20
_RECENT_DAYS = 30
_RECENT_LIMIT = 10

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

    def ids(self) -> set[str]:
        """Every id the model was shown — the evidence set a returned id is checked against."""
        found: set[str] = set()
        for row in (*self.companies, *self.projects, *self.tasks):
            for value in row.values():
                if value:
                    found.add(value.lower())
        return found

    def is_empty(self) -> bool:
        return not (self.companies or self.projects or self.tasks)

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


async def gather(ctx: RequestContext, text: str) -> ParseCandidates:
    """Resolve the shortlist for one quick-add line.

    Each block is gated on the permission its find tool declares — same visibility rule, one
    implementation — and every read rides the tenant repository, so a restricted login gets a
    shorter list rather than a wider one.
    """
    candidates = ParseCandidates()
    tokens = name_tokens(text)
    recent_companies, recent_projects, recent_tasks = (
        await _recent_ids(ctx) if ctx.can("time.entry.read") else (set(), set(), set())
    )

    if ctx.can("companies.company.read"):
        stmt = ctx.repo(Company).scoped_select()
        matches = [
            or_(Company.name.ilike(f"%{t}%"), Company.client_number.ilike(f"%{t}%"))
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

    if ctx.can("projects.project.read"):
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

    if ctx.can("tasks.task.read"):
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
    if ctx.can("time.entry.read"):
        candidates.entry_type_keys = await TimeEntryTypeService(ctx).active_keys()
    return candidates


__all__ = ["ParseCandidates", "gather", "name_tokens"]
