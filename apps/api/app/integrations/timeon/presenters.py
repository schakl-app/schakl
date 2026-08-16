"""Turning stored sync state into what a screen prints. Business-licensed — see LICENSE.

This file exists because of #300's finding, which was about a report and is really about every
surface an integration has: **a raw provider field must never reach a person.** A conflict row
whose diff reads ``{"seconds": {"local": 8100, "remote": 7200}}`` beside a pairing labelled
``3719717`` is a database dump wearing a screen's clothes, and no amount of tone fixes it. So
every id is resolved to a name here, every duration is rendered, and a compared field is named
in schakl's vocabulary on both sides.

The second reason is docs/PERFORMANCE.md's. Resolving those names one row at a time is the shape
that passes every functional test at three rows and issues four hundred queries at two hundred —
so each presenter batch-loads exactly one lookup per entity type it needs, and the whole
workspace payload is a fixed number of queries whatever it holds.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.auth.models import User
from app.core.tenancy import RequestContext
from app.integrations.timeon.models import (
    TimeonConflict,
    TimeonConflictStatus,
    TimeonLink,
    TimeonLinkKind,
    TimeonSyncRun,
)
from app.integrations.timeon.schemas import (
    TimeonAccountRead,
    TimeonConflictRead,
    TimeonLinkRead,
    TimeonSyncRunRead,
    TimeonWorkspaceRead,
)
from app.integrations.timeon.service import TimeonAccountService
from app.modules.companies.models import Company
from app.modules.projects.models import Project
from app.modules.time.models import TimeEntry


def _display(user: User | None) -> str | None:
    if user is None:
        return None
    return user.full_name or user.email


async def _users_by_id(ctx: RequestContext, ids: set[uuid.UUID]) -> dict[uuid.UUID, User]:
    """One query for every person a page names.

    ``users`` is instance-level and has no RLS, which is why this is a plain select rather than
    a scoped one — and why it is fed only ids that came out of tenant-scoped rows.
    """
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await ctx.session.execute(select(User).where(User.id.in_(ids)))).scalars().all()
    return {u.id: u for u in rows}


async def _companies_by_id(
    ctx: RequestContext, ids: set[uuid.UUID]
) -> dict[uuid.UUID, Company]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (
        await ctx.session.execute(
            ctx.repo(Company).scoped_select().where(Company.id.in_(ids))
        )
    ).scalars().all()
    return {c.id: c for c in rows}


async def present_runs(
    ctx: RequestContext, rows: Sequence[TimeonSyncRun]
) -> list[TimeonSyncRunRead]:
    """A run, with the person who asked for it named.

    ``actor_name`` is resolved live rather than snapshotted, unlike the activity trail's (§16):
    a run row is pruned within weeks, so the trail's argument — that an audit whose actor
    evaporates is not an audit — does not apply, and a live join keeps a renamed colleague's runs
    reading correctly.
    """
    users = await _users_by_id(ctx, {r.actor_user_id for r in rows if r.actor_user_id})
    return [
        TimeonSyncRunRead(
            **{
                **{
                    key: getattr(row, key)
                    for key in (
                        "id", "account_id", "kind", "dry_run", "ok", "window_from",
                        "window_to", "counts", "errors", "warnings", "message",
                        "created_at", "finished_at", "actor_user_id",
                    )
                },
                "actor_name": _display(users.get(row.actor_user_id)) if row.actor_user_id else None,
            }
        )
        for row in rows
    ]


async def present_links(
    ctx: RequestContext,
    *,
    account_id: uuid.UUID | None,
    kind: str | None,
    status: str | None,
    limit: int,
    offset: int,
) -> list[TimeonLinkRead]:
    """Pairings, with the schakl side named rather than pointed at.

    A pairing list whose local column is a uuid answers no question anybody has. What a person
    needs is *which* project, *whose* hours, *which* client — so one batch per kind resolves the
    label, and a pairing whose local row has since been deleted says so by leaving it null rather
    than by rendering a dangling id.
    """
    repo = ctx.repo(TimeonLink)
    stmt = repo.scoped_select().order_by(
        TimeonLink.external_date.desc().nullslast(), TimeonLink.created_at.desc()
    )
    if account_id is not None:
        stmt = stmt.where(TimeonLink.account_id == account_id)
    if kind:
        stmt = stmt.where(TimeonLink.kind == kind)
    if status:
        stmt = stmt.where(TimeonLink.status == status)
    rows = list(
        (await ctx.session.execute(stmt.limit(limit).offset(offset))).scalars().all()
    )
    labels = await _local_labels(ctx, rows)
    companies = await _companies_by_id(ctx, {r.company_id for r in rows if r.company_id})
    return [
        TimeonLinkRead(
            **{
                key: getattr(row, key)
                for key in (
                    "id", "account_id", "kind", "status", "origin", "local_id", "company_id",
                    "external_id", "external_name", "external_date", "observed", "observed_at",
                    "pushed_at", "pulled_at", "last_error",
                )
            },
            local_label=labels.get(row.id),
            company_name=(
                companies[row.company_id].name if row.company_id in companies else None
            ),
        )
        for row in rows
    ]


async def _local_labels(
    ctx: RequestContext, rows: Sequence[TimeonLink]
) -> dict[uuid.UUID, str]:
    """One batch per kind, never one query per row."""
    wanted: dict[str, set[uuid.UUID]] = {}
    for row in rows:
        if row.local_id:
            wanted.setdefault(row.kind, set()).add(row.local_id)
    out: dict[uuid.UUID, str] = {}
    by_kind: dict[str, dict[uuid.UUID, str]] = {}

    if ids := wanted.get(TimeonLinkKind.USER.value):
        by_kind[TimeonLinkKind.USER.value] = {
            uid: _display(user) or "" for uid, user in (await _users_by_id(ctx, ids)).items()
        }
    if ids := wanted.get(TimeonLinkKind.CUSTOMER.value):
        by_kind[TimeonLinkKind.CUSTOMER.value] = {
            cid: company.name for cid, company in (await _companies_by_id(ctx, ids)).items()
        }
    if ids := wanted.get(TimeonLinkKind.PROJECT.value):
        projects = (
            await ctx.session.execute(
                ctx.repo(Project).scoped_select().where(Project.id.in_(ids))
            )
        ).scalars().all()
        by_kind[TimeonLinkKind.PROJECT.value] = {p.id: p.name for p in projects}
    if ids := wanted.get(TimeonLinkKind.HOUR.value):
        entries = (
            await ctx.session.execute(
                ctx.repo(TimeEntry).scoped_select().where(TimeEntry.id.in_(ids))
            )
        ).scalars().all()
        by_kind[TimeonLinkKind.HOUR.value] = {
            e.id: f"{e.minutes // 60}:{e.minutes % 60:02d} — {(e.description or '').strip()}".strip(
                " —"
            )
            for e in entries
        }

    for row in rows:
        if row.local_id:
            label = by_kind.get(row.kind, {}).get(row.local_id)
            if label:
                out[row.id] = label
    return out


async def present_conflicts(
    ctx: RequestContext,
    *,
    account_id: uuid.UUID | None = None,
    status: str | None = "open",
    limit: int = 50,
    offset: int = 0,
    conflict_ids: list[uuid.UUID] | None = None,
) -> list[TimeonConflictRead]:
    """The queue, with enough on each row to settle it without opening anything else.

    Whose hours, which client, what differs, and both versions — because a queue that makes you
    open two other screens per row is a queue that stays full.
    """
    repo = ctx.repo(TimeonConflict)
    stmt = repo.scoped_select().order_by(TimeonConflict.detected_at.desc())
    if conflict_ids:
        stmt = stmt.where(TimeonConflict.id.in_(conflict_ids))
    else:
        if account_id is not None:
            stmt = stmt.where(TimeonConflict.account_id == account_id)
        if status:
            stmt = stmt.where(TimeonConflict.status == status)
        stmt = stmt.limit(limit).offset(offset)
    rows = list((await ctx.session.execute(stmt)).scalars().all())
    if not rows:
        return []

    links = {
        link.id: link
        for link in (
            await ctx.session.execute(
                ctx.repo(TimeonLink).scoped_select().where(
                    TimeonLink.id.in_([r.link_id for r in rows])
                )
            )
        ).scalars().all()
    }
    entry_ids = {
        link.local_id for link in links.values() if link.local_id
    }
    entries = {
        e.id: e
        for e in (
            await ctx.session.execute(
                ctx.repo(TimeEntry).scoped_select().where(TimeEntry.id.in_(entry_ids))
            )
        ).scalars().all()
    } if entry_ids else {}
    users = await _users_by_id(
        ctx,
        {e.user_id for e in entries.values()}
        | {r.resolved_by_user_id for r in rows if r.resolved_by_user_id},
    )
    companies = await _companies_by_id(ctx, {r.company_id for r in rows if r.company_id})

    out: list[TimeonConflictRead] = []
    for row in rows:
        link = links.get(row.link_id)
        entry = entries.get(link.local_id) if link and link.local_id else None
        out.append(
            TimeonConflictRead(
                id=row.id,
                account_id=row.account_id,
                link_id=row.link_id,
                kind=TimeonLinkKind(link.kind) if link else TimeonLinkKind.HOUR,
                status=TimeonConflictStatus(row.status),
                company_id=row.company_id,
                company_name=(
                    companies[row.company_id].name if row.company_id in companies else None
                ),
                differences=row.differences,
                local_snapshot=row.local_snapshot,
                remote_snapshot=row.remote_snapshot,
                detected_at=row.detected_at,
                resolved_at=row.resolved_at,
                resolved_by_user_id=row.resolved_by_user_id,
                resolved_by_name=_display(users.get(row.resolved_by_user_id))
                if row.resolved_by_user_id
                else None,
                note=row.note,
                user_name=_display(users.get(entry.user_id)) if entry else None,
                local_id=link.local_id if link else None,
                external_id=link.external_id if link else None,
            )
        )
    return out


async def workspace_payload(
    ctx: RequestContext, account_id: uuid.UUID | None
) -> TimeonWorkspaceRead:
    """The sync page's shell: connections, the last run of each, and what is waiting.

    One round trip because four reads that each resolve the same account are four round trips for
    one screen (docs/GOOGLE_TAG_MANAGER.md §3a). Nothing here calls Timeon — so it is fast, and
    it renders during an outage, which is exactly when somebody opens it.
    """
    service = TimeonAccountService(ctx)
    accounts = [TimeonAccountRead(**row) for row in await service.list_accounts()]
    if account_id is not None:
        accounts = [a for a in accounts if a.id == account_id]

    runs: list[TimeonSyncRunRead] = []
    conflicts: list[TimeonConflictRead] = []
    if accounts:
        ids = [a.id for a in accounts]
        rows = (
            await ctx.session.execute(
                ctx.repo(TimeonSyncRun)
                .scoped_select()
                .where(TimeonSyncRun.account_id.in_(ids))
                .order_by(TimeonSyncRun.created_at.desc())
                .limit(10)
            )
        ).scalars().all()
        runs = await present_runs(ctx, list(rows))
        conflicts = await present_conflicts(
            ctx, account_id=accounts[0].id if len(accounts) == 1 else None, limit=10
        )
    return TimeonWorkspaceRead(
        accounts=accounts,
        recent_runs=runs,
        open_conflicts=conflicts,
        server_time=datetime.now(UTC),
    )
