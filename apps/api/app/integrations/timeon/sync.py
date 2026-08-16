"""The Timeon sync engine. Business-licensed — see LICENSE.

``docs/TIMEON.md`` holds the decisions; this file implements them. Nine rules govern every
path through it, and each one is here because getting it wrong is expensive rather than untidy.

**1. Adoption comes before creation, always.** The first run against an instance that already
holds Timeon history — which is every instance, because the migration importer wrote 2814
entries here before this module existed — must *recognise* those entries and pair them without
writing a byte. It matches on the importer's own natural key (``mapping.natural_key``), which
is why that function is byte-identical to the one in ``apps/api/scripts/timeon_import.py``. A
sync that created before it adopted would double three years of somebody's timesheet on its
first press, and no undo exists for that.

**2. A window is the sync.** Timeon's hour rows carry ``createdOn`` and no modified timestamp,
so "what changed since last night" is not a question its API can answer (``client.py`` rule 4).
The run re-reads a date window and compares fingerprints. That makes the window a real horizon —
so it is stored on the run, shown on the screen, and never implied.

**3. Absence is a deletion only inside a window we know we read completely, and only after
asking again.** ``filter.deleted`` is accepted and ignored by Timeon (rule 5), so a delete has
no signal but absence. Two guards make that safe: :meth:`TimeonClient.hours` refuses a window
whose row count disagrees with the server's own total, and a row that has vanished from its
window is re-read *by id* before anything is deleted — because an hour moved from 5 August to 5
January is absent from August and is not gone.

**4. Both sides moved is a conflict, and a conflict is a stored decision.** Not a recomputation:
a queue that re-proposes the same twelve rows every night is one nobody reads by the third week
(#318).

**5. An invoiced entry is a record, not live data.** ``docs/TIMEON.md`` §2 argued *against*
building this at all on exactly that ground, and ``protect_invoiced`` is the answer to the
argument rather than a dismissal of it: nothing that has reached a client's invoice is ever
rewritten or deleted by a pull. The divergence is reported.

**6. An unmapped person is reported, never guessed.** A third of the migration's hours belonged
to people with no schakl account. Filing them under whoever ran the sync destroys per-employee
reporting silently; skipping them and saying so is loud, correctable and harms nothing.

**7. A push sends the whole row.** ``hour/save`` replaces rather than patches, measured. Fields
schakl has no concept of — distance, expenses, the category — are carried over from what we last
observed, or a description correction would delete a client's mileage claim.

**8. A row-level refusal is reported; a call-level failure stops the run.** §18's split. One
protected entry is one line in the report; a credential that stopped working is not forty
thousand lines, it is one message and a run that is not ``ok``.

**9. A dry run is the default.** Every counter is computed and nothing is written, so an agency
can see exactly what turning this on would do before it does it (#305 — show the constraint
working rather than removing the control).
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import select

from app.core.tenancy import RequestContext
from app.integrations.timeon.client import TimeonClient, TimeonError
from app.integrations.timeon.mapping import (
    Resolver,
    differences,
    fingerprint,
    natural_key,
    neutral_from_entry,
    neutral_from_row,
    observed_of,
    parse_timeon_ts,
    plan_start_seconds,
    row_date,
    start_seconds_of,
    started_at_for,
    timeon_payload,
)
from app.integrations.timeon.models import (
    ConflictPolicy,
    SyncDirection,
    TimeonAccount,
    TimeonConflict,
    TimeonConflictStatus,
    TimeonLink,
    TimeonLinkKind,
    TimeonLinkOrigin,
    TimeonLinkStatus,
    TimeonSyncKind,
    TimeonSyncRun,
)
from app.integrations.timeon.service import client_for
from app.modules.companies.models import Company
from app.modules.projects.models import Project, ProjectStatus
from app.modules.time.models import TimeEntry

logger = logging.getLogger("schakl.timeon")

#: A run stores at most this many of each list. A sync against a broken mapping would otherwise
#: write one entry per row in the organisation into a JSONB column nobody reads past the
#: fortieth — and the counts stay exact either way, which is the half that matters.
MAX_REPORTED = 60


class RunReport:
    """What a run counts, refuses and needs from a human.

    Three lists rather than one, because they need three different reactions. ``errors`` is
    something that went wrong. ``warnings`` is the run telling an admin what it could not decide
    — an unmapped person, a client Timeon has and schakl does not — which is not a failure and
    must not make a run red. ``blocked`` is a refusal *by design*: a protected entry the policy
    says not to touch, reported so the protection is visible rather than silent.
    """

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.errors: list[dict[str, Any]] = []
        self.warnings: list[dict[str, Any]] = []

    def error(self, code: str, **detail: Any) -> None:
        self.counts["failed"] += 1
        if len(self.errors) < MAX_REPORTED:
            self.errors.append({"code": code, **detail})

    def warn(self, code: str, **detail: Any) -> None:
        self.counts[f"warn_{code}"] += 1
        if len(self.warnings) < MAX_REPORTED:
            self.warnings.append({"code": code, **detail})

    @property
    def ok(self) -> bool:
        return not self.errors


class TimeonSyncService:
    """One sync, against one connected organisation.

    Constructed per run. It holds the client, the resolver and the report, because every one of
    those is scoped to the run and sharing them across runs is how a second sync inherits the
    first one's stale id map.
    """

    def __init__(self, ctx: RequestContext, account: TimeonAccount) -> None:
        self.ctx = ctx
        self.account = account
        self.client: TimeonClient = client_for(account)
        self.links = ctx.repo(TimeonLink)
        self.conflicts = ctx.repo(TimeonConflict)
        self.report = RunReport()
        self._resolver: Resolver | None = None
        #: Pairings this run resolved, whether or not it was allowed to *store* them.
        #:
        #: A dry run writes no link rows, so a resolver built from the database alone would find
        #: no owner for any hour and report the whole window as "skipped: unmapped user" — which
        #: is a dry run that cannot say what the real run would do, and therefore not a dry run.
        #: The reference phase records what it worked out here, and :meth:`resolver` merges it
        #: over the stored links, so both modes resolve identically.
        self._pairs: dict[str, dict[str, uuid.UUID]] = {}

    # ------------------------------------------------------------------ entry point
    async def run(
        self,
        *,
        kind: TimeonSyncKind,
        dry_run: bool,
        window_from: date | None = None,
        window_to: date | None = None,
        actor_user_id: uuid.UUID | None = None,
    ) -> TimeonSyncRun:
        """Do one run and return its record.

        The record is written **first**, with ``ok=False``, and finished at the end. A run that
        dies mid-flight therefore leaves a row saying so rather than leaving no trace at all —
        the state an integration is least able to explain afterwards. (``require_context`` rolls
        back on an exception, so the route catches; see :meth:`_finish`.)
        """
        start, end = self._window(window_from, window_to)
        run = await self.ctx.repo(TimeonSyncRun).create(
            account_id=self.account.id,
            kind=kind.value,
            dry_run=dry_run,
            ok=False,
            window_from=start,
            window_to=end,
            actor_user_id=actor_user_id,
        )
        try:
            # References always: an hour that cannot name its owner, its client or its project
            # cannot be reconciled at all, so every kind of run pays for them. Projects are read
            # once, with `create` decided by the kind rather than by a second pass — two reads of
            # 157 projects to answer one question is a rate limit spent on bookkeeping.
            await self._sync_references(
                dry_run=dry_run,
                create_projects=kind in (TimeonSyncKind.FULL, TimeonSyncKind.PROJECTS),
            )
            if kind in (TimeonSyncKind.FULL, TimeonSyncKind.HOURS, TimeonSyncKind.ADOPT):
                await self._sync_hours(
                    start, end, dry_run=dry_run, adopt_only=kind is TimeonSyncKind.ADOPT
                )
        except TimeonError as exc:
            # A call-level failure: the credential lapsed, the edge blocked us, the host did not
            # answer. One message, not forty thousand rows (rule 8).
            return await self._finish(run, message=str(exc)[:500], ok=False, dry_run=dry_run)
        return await self._finish(run, ok=self.report.ok, dry_run=dry_run)

    def _window(self, start: date | None, end: date | None) -> tuple[date, date]:
        """The span this run covers, with the history floor applied.

        An explicit window is honoured (a repair of one month, a full resync); otherwise it is
        the account's rolling ``window_days`` back from today. The floor clamps either — nothing
        on or before it is read, written or deleted, which is what keeps a sync away from three
        years of settled history the migration already marked invoiced.
        """
        today = datetime.now(UTC).date()
        win_end = end or today
        win_start = start or (win_end - timedelta(days=max(1, self.account.window_days)))
        floor = self.account.history_floor
        if floor is not None and win_start <= floor:
            win_start = floor + timedelta(days=1)
        if win_end < win_start:
            win_end = win_start
        return win_start, win_end

    async def _finish(
        self, run: TimeonSyncRun, *, ok: bool, dry_run: bool, message: str | None = None
    ) -> TimeonSyncRun:
        now = datetime.now(UTC)
        run = await self.ctx.repo(TimeonSyncRun).update(
            run,
            ok=ok,
            counts=dict(self.report.counts),
            errors=self.report.errors,
            warnings=self.report.warnings,
            message=message,
            finished_at=now,
        )
        if not dry_run:
            values: dict[str, Any] = {"last_pull_at": now}
            if self.report.counts.get("pushed") or self.report.counts.get("pushed_new"):
                values["last_push_at"] = now
            values["last_error"] = message
            await self.ctx.repo(TimeonAccount).update(self.account, **values)
        return run

    # ------------------------------------------------------------------ references
    async def _sync_references(self, *, dry_run: bool, create_projects: bool = False) -> None:
        """Pair the three things every hour row points at: a person, a client, a project.

        Read even on an hours-only run, because an hour that cannot name its owner cannot be
        written at all — and re-derived rather than trusted, because an e-mail address changes
        and a stored pairing that is never re-checked is one that silently points at the wrong
        person. The *pairing* is stored (a link row); what is re-derived is whether it still
        holds.
        """
        await self._pair_users(dry_run=dry_run)
        await self._pair_customers(dry_run=dry_run)
        await self._pair_projects(dry_run=dry_run, create=create_projects)

    async def _existing_links(self, kind: TimeonLinkKind) -> dict[str, TimeonLink]:
        rows = (
            (
                await self.ctx.session.execute(
                    self.links.scoped_select()
                    .where(TimeonLink.account_id == self.account.id)
                    .where(TimeonLink.kind == kind.value)
                )
            )
            .scalars()
            .all()
        )
        return {row.external_id: row for row in rows}

    async def _pair_users(self, *, dry_run: bool) -> None:
        """Timeon user ↔ schakl user, on e-mail.

        E-mail rather than name, and the migration proved why in the other direction: two
        clients called *Maatschap Mini Camping Boudewijnskerke* exist in **both** systems, so a
        name match is ambiguous exactly where it looks safest. A person with no schakl account is
        a warning, never a substitution (rule 6).
        """
        from app.core.auth.models import User
        from app.core.models import Membership

        remote = await self.client.users()
        member_ids = {
            m.user_id
            for m in (
                await self.ctx.session.execute(
                    select(Membership).where(Membership.org_id == self.ctx.org.id)
                )
            )
            .scalars()
            .all()
        }
        by_email = {
            u.email.lower(): u
            for u in (
                await self.ctx.session.execute(select(User).where(User.id.in_(member_ids)))
            )
            .scalars()
            .all()
        } if member_ids else {}

        links = await self._existing_links(TimeonLinkKind.USER)
        for user in remote:
            ext = str(user.get("userID"))
            email = (user.get("email") or "").strip().lower()
            match = by_email.get(email)
            self.report.counts["users_read"] += 1
            if match is None:
                # Deliberately not created here even with `create_missing_users` on: an account
                # is a person and a membership may cost a seat, so it is the *hours* phase that
                # asks for one, and only when that person actually logged something in the
                # window. Creating seven dormant accounts because Timeon still lists somebody
                # who left in 2024 is a mess nobody asked for.
                self.report.warn("user_unmapped", email=email or None, name=user.get("name"),
                                 external_id=ext)
                continue
            await self._upsert_link(
                TimeonLinkKind.USER,
                external_id=ext,
                local_id=match.id,
                external_name=user.get("name"),
                dry_run=dry_run,
                existing=links.get(ext),
            )

    async def _pair_customers(self, *, dry_run: bool) -> None:
        """Timeon customer ↔ schakl company, on the client number.

        ``customerNumber`` ↔ ``client_number``: unique on both sides, no fuzzy matching, and the
        one field both systems agree is an identifier. All 108 of the migration's customers
        joined on it with no misses. Timeon's own ``externalID`` holds UUIDs from some earlier
        system and resolves to nothing here — it is ignored rather than guessed at.
        """
        remote = await self.client.customers()
        companies = (
            (await self.ctx.session.execute(self.ctx.repo(Company).scoped_select()))
            .scalars()
            .all()
        )
        by_number = {
            str(c.client_number).strip(): c for c in companies if c.client_number
        }
        links = await self._existing_links(TimeonLinkKind.CUSTOMER)
        for customer in remote:
            ext = str(customer.get("customerID"))
            number = str(customer.get("customerNumber") or "").strip()
            match = by_number.get(number)
            self.report.counts["customers_read"] += 1
            if match is None:
                self.report.warn(
                    "customer_unmapped",
                    name=customer.get("name"),
                    number=number or None,
                    external_id=ext,
                )
                continue
            await self._upsert_link(
                TimeonLinkKind.CUSTOMER,
                external_id=ext,
                local_id=match.id,
                company_id=match.id,
                external_name=customer.get("name"),
                dry_run=dry_run,
                existing=links.get(ext),
            )

    async def _pair_projects(self, *, dry_run: bool, create: bool) -> None:
        """Timeon project ↔ schakl project, on ``(client, name)``.

        Names are only safe *within* a client, which is why the client pairing runs first. An
        unpaired Timeon project is a warning by default; with ``create_missing_projects`` on and
        the direction allowing a pull, one is created — carrying its budget (Timeon states it in
        **seconds**) and its archived status, because a project closed there and open here is a
        difference somebody has to notice.
        """
        remote = await self.client.projects()
        customer_links = await self._existing_links(TimeonLinkKind.CUSTOMER)
        company_by_customer = {
            ext: link.local_id for ext, link in customer_links.items() if link.local_id
        }
        projects = (
            (await self.ctx.session.execute(self.ctx.repo(Project).scoped_select()))
            .scalars()
            .all()
        )
        by_key = {(p.company_id, (p.name or "").strip().lower()): p for p in projects}
        links = await self._existing_links(TimeonLinkKind.PROJECT)
        pull = self._direction(self.account.projects_direction) in ("pull", "two_way")

        for project in remote:
            ext = str(project.get("projectID"))
            self.report.counts["projects_read"] += 1
            company_id = company_by_customer.get(str(project.get("customerID")))
            if company_id is None:
                self.report.warn("project_no_client", name=project.get("name"), external_id=ext)
                continue
            match = by_key.get((company_id, (project.get("name") or "").strip().lower()))
            if match is None:
                if not (create and pull and self.account.create_missing_projects):
                    self.report.warn(
                        "project_unmapped", name=project.get("name"), external_id=ext
                    )
                    continue
                if dry_run:
                    self.report.counts["projects_would_create"] += 1
                    continue
                match = await self._create_project(project, company_id)
                by_key[(company_id, (match.name or "").strip().lower())] = match
                self.report.counts["projects_created"] += 1
            await self._upsert_link(
                TimeonLinkKind.PROJECT,
                external_id=ext,
                local_id=match.id,
                company_id=company_id,
                external_name=project.get("name"),
                dry_run=dry_run,
                existing=links.get(ext),
            )

    async def _create_project(self, project: dict[str, Any], company_id: uuid.UUID) -> Project:
        from app.modules.projects.schemas import ProjectCreate
        from app.modules.projects.service import ProjectService

        budget = (project.get("budget") or {}).get("budget")
        return await ProjectService(self.ctx).create(
            ProjectCreate(
                company_id=company_id,
                name=project.get("name") or "Timeon",
                status=(
                    ProjectStatus.ARCHIVED
                    if project.get("statusID") == 2
                    else ProjectStatus.ACTIVE
                ),
                billable_default=bool(project.get("defaultBillable")),
                # Timeon states a budget in seconds (302400 = 84:00).
                budget_hours=round(budget / 3600.0, 2) if budget else None,
                budget_period="total",
            )
        )

    # ------------------------------------------------------------------ resolver
    async def resolver(self) -> Resolver:
        """The three id maps, built once per run from the stored links **and this run's own**.

        Never per row: resolving "which schakl project is Timeon 2115429" with a query per hour
        is the shape docs/PERFORMANCE.md exists to prevent, and a 400-row window would issue
        1200 of them.

        :attr:`_pairs` is merged over the stored rows so a **dry run resolves identically to the
        real one**. Without it the first dry run against a fresh connection would find no owner
        for any hour — because a dry run stores no link — and report the entire window as
        "skipped: unmapped user", which is precisely the answer a dry run exists not to give.
        """
        if self._resolver is not None:
            return self._resolver
        rows = (
            (
                await self.ctx.session.execute(
                    self.links.scoped_select()
                    .where(TimeonLink.account_id == self.account.id)
                    .where(TimeonLink.kind != TimeonLinkKind.HOUR.value)
                    .where(TimeonLink.local_id.isnot(None))
                )
            )
            .scalars()
            .all()
        )
        users: dict[str, uuid.UUID] = {}
        companies: dict[str, uuid.UUID] = {}
        projects: dict[str, uuid.UUID] = {}
        for row in rows:
            target = {
                TimeonLinkKind.USER.value: users,
                TimeonLinkKind.CUSTOMER.value: companies,
                TimeonLinkKind.PROJECT.value: projects,
            }.get(row.kind)
            if target is not None and row.local_id is not None:
                target[row.external_id] = row.local_id
        users.update(self._pairs.get(TimeonLinkKind.USER.value, {}))
        companies.update(self._pairs.get(TimeonLinkKind.CUSTOMER.value, {}))
        projects.update(self._pairs.get(TimeonLinkKind.PROJECT.value, {}))
        self._resolver = Resolver(users=users, companies=companies, projects=projects)
        return self._resolver

    # ------------------------------------------------------------------ hours
    async def _sync_hours(
        self, start: date, end: date, *, dry_run: bool, adopt_only: bool
    ) -> None:
        """Reconcile one window of hours. The whole engine, in one narrative.

        ``adopt_only`` is the safe first press: pair what is already here on both sides and stop.
        It writes link rows and nothing else, so an agency can look at "2814 paired, 3 only in
        Timeon, 1 only here" before deciding what the sync should do about the difference.
        """
        resolver = await self.resolver()
        direction = self._direction(self.account.hours_direction)
        may_pull = direction in ("pull", "two_way")
        may_push = direction in ("push", "two_way")

        remote_rows = await self.client.hours(start, end)
        floor = self.account.history_floor
        remote_rows = [
            r for r in remote_rows
            if (d := row_date(r)) is not None and (floor is None or d > floor)
        ]
        starts = plan_start_seconds(remote_rows)
        remote_by_id = {str(r.get("hourID")): r for r in remote_rows}
        self.report.counts["remote_read"] = len(remote_rows)

        links = await self._hour_links(start, end)
        by_ext = {link.external_id: link for link in links}
        linked_local_ids = {link.local_id for link in links if link.local_id}

        entries = await self._local_entries(start, end, extra_ids=linked_local_ids)
        by_local = {link.local_id: link for link in links if link.local_id}
        self.report.counts["local_read"] = len(entries)

        # --- 1. adoption (rule 1) --------------------------------------------- #
        await self._adopt(remote_rows, entries, by_ext, by_local, starts, resolver,
                          dry_run=dry_run)
        if adopt_only:
            return

        # --- 2. every remote row ---------------------------------------------- #
        for row in remote_rows:
            ext = str(row.get("hourID"))
            link = by_ext.get(ext)
            if link is None:
                if may_pull:
                    await self._pull_new(row, starts, resolver, dry_run=dry_run)
                else:
                    self.report.counts["remote_only"] += 1
                continue
            entry = entries.get(link.local_id) if link.local_id else None
            if entry is None:
                await self._local_gone(link, row, may_push=may_push, dry_run=dry_run)
                continue
            await self._reconcile(link, entry, row, starts, resolver,
                                  may_pull=may_pull, may_push=may_push, dry_run=dry_run)

        # --- 3. local entries with no pairing ---------------------------------- #
        for entry in entries.values():
            if entry.id in by_local:
                continue
            if entry.started_at.astimezone(UTC).date() > end:
                continue
            if may_push:
                await self._push_new(entry, resolver, dry_run=dry_run)
            else:
                self.report.counts["local_only"] += 1

        # --- 4. links whose remote row has vanished (rule 3) -------------------- #
        vanished = [
            link for link in links
            if link.external_id not in remote_by_id
            and link.status != TimeonLinkStatus.IGNORED.value
        ]
        await self._handle_vanished(vanished, entries, may_pull=may_pull, dry_run=dry_run)

    async def _hour_links(self, start: date, end: date) -> list[TimeonLink]:
        stmt = (
            self.links.scoped_select()
            .where(TimeonLink.account_id == self.account.id)
            .where(TimeonLink.kind == TimeonLinkKind.HOUR.value)
            .where(TimeonLink.external_date >= start)
            .where(TimeonLink.external_date <= end)
        )
        return list((await self.ctx.session.execute(stmt)).scalars().all())

    async def _local_entries(
        self, start: date, end: date, *, extra_ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, TimeEntry]:
        """Entries in the window, **plus** those a window link points at.

        The union matters: an entry somebody moved out of the window still has a link inside it,
        and loading only by date would find no local row and read that as a deletion.
        """
        repo = self.ctx.repo(TimeEntry)
        lo = datetime.combine(start, time.min, tzinfo=UTC)
        hi = datetime.combine(end, time.max, tzinfo=UTC)
        stmt = (
            repo.scoped_select()
            .where(TimeEntry.started_at >= lo)
            .where(TimeEntry.started_at <= hi)
            .where(TimeEntry.ended_at.isnot(None))
        )
        rows = list((await self.ctx.session.execute(stmt)).scalars().all())
        seen = {r.id for r in rows}
        missing = [i for i in extra_ids if i not in seen]
        if missing:
            extra = (
                await self.ctx.session.execute(
                    repo.scoped_select().where(TimeEntry.id.in_(missing))
                )
            ).scalars().all()
            rows.extend(extra)
        return {r.id: r for r in rows}

    # --- adoption ---------------------------------------------------------- #
    async def _adopt(
        self,
        remote_rows: list[dict[str, Any]],
        entries: dict[uuid.UUID, TimeEntry],
        by_ext: dict[str, TimeonLink],
        by_local: dict[uuid.UUID, TimeonLink],
        starts: dict[int, int],
        resolver: Resolver,
        *,
        dry_run: bool,
    ) -> None:
        """Pair rows that are plainly already the same entry, writing nothing to either side.

        The natural key is the migration importer's, so the 2814 entries it wrote are recognised
        exactly. Duplicates are handled by *counting* rather than by pretending the key is
        injective: the corpus holds one genuine collision (a pair of identical two-hour entries
        with no remark), and pairing them in a stable order is right in both directions.
        """
        candidates: dict[tuple[Any, ...], list[TimeEntry]] = {}
        for entry in entries.values():
            if entry.id in by_local:
                continue
            key = natural_key(
                entry.user_id, entry.started_at, entry.minutes, entry.project_id,
                entry.description,
            )
            candidates.setdefault(key, []).append(entry)
        for bucket in candidates.values():
            bucket.sort(key=lambda e: (e.started_at, e.id))

        for row in sorted(remote_rows, key=lambda r: int(r.get("hourID") or 0)):
            ext = str(row.get("hourID"))
            if ext in by_ext:
                continue
            owner = resolver.user_by_ext.get(str(row.get("userID")))
            day = row_date(row)
            if owner is None or day is None:
                continue
            project_id = resolver.project_by_ext.get(str(row.get("projectID") or "")) or None
            key = natural_key(
                owner,
                started_at_for(day, starts.get(int(row.get("hourID") or 0), 0)),
                int(row.get("seconds") or 0) // 60,
                project_id,
                row.get("remark"),
            )
            bucket = candidates.get(key)
            if not bucket:
                continue
            entry = bucket.pop(0)
            self.report.counts["adopted"] += 1
            if dry_run:
                continue
            link = await self._write_hour_link(
                entry=entry, row=row, starts=starts, resolver=resolver,
                origin=TimeonLinkOrigin.ADOPTED,
            )
            by_ext[ext] = link
            by_local[entry.id] = link

    # --- the four outcomes --------------------------------------------------- #
    async def _reconcile(
        self,
        link: TimeonLink,
        entry: TimeEntry,
        row: dict[str, Any],
        starts: dict[int, int],
        resolver: Resolver,
        *,
        may_pull: bool,
        may_push: bool,
        dry_run: bool,
    ) -> None:
        """One paired record: work out which side moved, and act on the answer (rule 4)."""
        has_remote_start = row.get("fromSeconds") is not None
        remote = neutral_from_row(
            row, start_seconds=starts.get(int(row.get("hourID") or 0)), resolver=resolver
        )
        local = neutral_from_entry(entry, resolver=resolver, has_remote_start=has_remote_start)
        remote_hash, local_hash = fingerprint(remote), fingerprint(local)

        # `differences`, not `remote_hash == local_hash`: the two hashes answer "did *this* side
        # move" (below) and a sentinel makes them unequal for a difference no direction can act
        # on. See `mapping.differences` for what that cost on the first real run.
        if not differences(local, remote):
            self.report.counts["in_step"] += 1
            if not dry_run and link.status != TimeonLinkStatus.LINKED.value:
                await self.links.update(
                    link,
                    status=TimeonLinkStatus.LINKED.value,
                    local_hash=local_hash,
                    remote_hash=remote_hash,
                    observed=observed_of(row),
                    observed_at=datetime.now(UTC),
                    last_error=None,
                )
            elif not dry_run:
                await self.links.update(
                    link, observed=observed_of(row), observed_at=datetime.now(UTC),
                    local_hash=local_hash, remote_hash=remote_hash,
                )
            await self._reconcile_approval(link, entry, row, dry_run=dry_run)
            return

        remote_moved = link.remote_hash is not None and link.remote_hash != remote_hash
        local_moved = link.local_hash is not None and link.local_hash != local_hash
        if link.remote_hash is None or link.local_hash is None:
            # A pairing that has never agreed on anything — adopted from a natural-key match
            # that ignores billable, or created before this ran. Treat the *remote* as the side
            # that moved when only it differs from what we would have written, which is the
            # conservative reading: pulling is reversible from Timeon, pushing is not.
            remote_moved, local_moved = True, False

        if not remote_moved and not local_moved:
            # They differ, and neither has moved since the two sides last agreed to differ.
            # That is what a **dismissed** conflict leaves behind: "these two rows are allowed
            # to be different" is a real decision (#318), and re-deriving it every night is the
            # queue nobody reads. Without this branch the run would fall through to the push
            # arm below and quietly overwrite the difference somebody deliberately kept.
            self.report.counts["tolerated"] += 1
            return

        if remote_moved and local_moved:
            await self._on_conflict(
                link, entry, row, local, remote,
                may_pull=may_pull, may_push=may_push, dry_run=dry_run,
            )
            return
        if remote_moved:
            if not may_pull:
                await self._mark_drift(link, "remote", dry_run=dry_run)
                return
            await self._apply_pull(link, entry, row, starts, resolver, dry_run=dry_run)
            return
        if not may_push:
            await self._mark_drift(link, "local", dry_run=dry_run)
            return
        await self._apply_push(link, entry, row, resolver, dry_run=dry_run)

    async def _mark_drift(self, link: TimeonLink, side: str, *, dry_run: bool) -> None:
        """One side moved and the direction forbids carrying it. Reported, never silent.

        This is the state a one-way sync spends most of its life in and the one an integration is
        most tempted to swallow: schakl's own edits under ``pull`` are *supposed* to stay put.
        Saying so is what keeps "why is Timeon still showing the old description" answerable.
        """
        self.report.counts[f"drift_{side}"] += 1
        if not dry_run and link.status != TimeonLinkStatus.DRIFT.value:
            await self.links.update(link, status=TimeonLinkStatus.DRIFT.value)

    async def _apply_pull(
        self,
        link: TimeonLink,
        entry: TimeEntry,
        row: dict[str, Any],
        starts: dict[int, int],
        resolver: Resolver,
        *,
        dry_run: bool,
    ) -> None:
        """Carry Timeon's version onto the schakl entry — unless the entry is protected."""
        blocked = self._protection(entry)
        if blocked:
            self.report.counts[f"protected_{blocked}"] += 1
            self.report.warn("protected", reason=blocked, entry_id=str(entry.id),
                             external_id=link.external_id)
            if not dry_run:
                await self.links.update(link, status=TimeonLinkStatus.DRIFT.value)
            return
        self.report.counts["pulled"] += 1
        if dry_run:
            return
        from app.modules.time.system import revise_entry

        day = row_date(row)
        assert day is not None
        seconds = starts.get(int(row.get("hourID") or 0), 0)
        company_id = resolver.company_by_ext.get(str(row.get("customerID") or ""))
        project_id = resolver.project_by_ext.get(str(row.get("projectID") or ""))
        touch = {"started_at", "minutes", "description", "billable"}
        # A reference we cannot resolve is left alone rather than cleared: `?` means "Timeon
        # points at something schakl has no pairing for", and writing that as NULL would detach
        # an entry from its client because a *project* was never paired.
        if not row.get("customerID") or company_id is not None:
            touch.add("company_id")
        if not row.get("projectID") or project_id is not None:
            touch.add("project_id")
        await revise_entry(
            self.ctx,
            entry,
            started_at=started_at_for(day, seconds),
            minutes=int(row.get("seconds") or 0) // 60,
            company_id=company_id,
            project_id=project_id,
            description=(row.get("remark") or "").strip() or None,
            billable=bool(row.get("billable")),
            touch=frozenset(touch),
        )
        await self._settle(link, entry, row, starts, resolver, pulled=True)

    async def _apply_push(
        self,
        link: TimeonLink,
        entry: TimeEntry,
        row: dict[str, Any],
        resolver: Resolver,
        *,
        dry_run: bool,
    ) -> None:
        """Carry schakl's version onto the Timeon row — unless Timeon has locked it."""
        if row.get("invoiceID"):
            self.report.counts["remote_invoiced"] += 1
            self.report.warn("remote_invoiced", external_id=link.external_id,
                             invoice=row.get("invoiceNr"))
            if not dry_run:
                await self.links.update(link, status=TimeonLinkStatus.DRIFT.value)
            return
        self.report.counts["pushed"] += 1
        if dry_run:
            return
        try:
            saved = await self._save_remote(entry, resolver, observed=link.observed,
                                            hour_id=int(link.external_id))
        except TimeonError as exc:
            self.report.error("push_failed", entry_id=str(entry.id), detail=str(exc)[:200])
            await self.links.update(
                link, status=TimeonLinkStatus.ERROR.value, last_error=str(exc)[:500]
            )
            return
        await self._settle(link, entry, saved or row, {}, resolver, pushed=True)

    async def _on_conflict(
        self,
        link: TimeonLink,
        entry: TimeEntry,
        row: dict[str, Any],
        local: dict[str, Any],
        remote: dict[str, Any],
        *,
        may_pull: bool,
        may_push: bool,
        dry_run: bool,
    ) -> None:
        """Both sides moved. The policy decides whether a human does.

        ``schakl_wins`` / ``timeon_wins`` are a tenant saying "there is an authoritative side
        here, stop asking me" — a real arrangement, and one they choose rather than one inferred
        from which side happens to have been edited more recently. ``manual`` writes the conflict
        down and touches neither side.
        """
        policy = self.account.conflict_policy
        if policy == ConflictPolicy.TIMEON_WINS.value and may_pull:
            self.report.counts["conflict_auto_remote"] += 1
            await self._apply_pull(link, entry, row, {}, await self.resolver(), dry_run=dry_run)
            return
        if policy == ConflictPolicy.SCHAKL_WINS.value and may_push:
            self.report.counts["conflict_auto_local"] += 1
            await self._apply_push(link, entry, row, await self.resolver(), dry_run=dry_run)
            return

        self.report.counts["conflicts"] += 1
        if dry_run:
            return
        diff = differences(local, remote)
        existing = (
            await self.ctx.session.execute(
                self.conflicts.scoped_select()
                .where(TimeonConflict.link_id == link.id)
                .where(TimeonConflict.status == TimeonConflictStatus.OPEN.value)
                .limit(1)
            )
        ).scalars().first()
        snapshot_local = {**local, "user": str(entry.user_id), "entry_id": str(entry.id)}
        snapshot_remote = {**remote, "hour_id": row.get("hourID"), "user": row.get("user")}
        if existing is not None:
            # A second detection *updates* rather than stacking: the same two rows diverging
            # further is still one decision to make (the partial unique index says so too).
            await self.conflicts.update(
                existing,
                differences=diff,
                local_snapshot=snapshot_local,
                remote_snapshot=snapshot_remote,
                detected_at=datetime.now(UTC),
            )
        else:
            await self.conflicts.create(
                account_id=self.account.id,
                link_id=link.id,
                company_id=entry.company_id,
                differences=diff,
                local_snapshot=snapshot_local,
                remote_snapshot=snapshot_remote,
                detected_at=datetime.now(UTC),
            )
        await self.links.update(link, status=TimeonLinkStatus.CONFLICT.value)

    # --- creations and deletions --------------------------------------------- #
    async def _pull_new(
        self,
        row: dict[str, Any],
        starts: dict[int, int],
        resolver: Resolver,
        *,
        dry_run: bool,
    ) -> None:
        """A Timeon row schakl has never seen. Create the entry, or say why not."""
        day = row_date(row)
        minutes = int(row.get("seconds") or 0) // 60
        if day is None or minutes <= 0:
            # schakl rejects a zero-length span, and Timeon holds eight of them. Reported, not
            # skipped in silence: eight rows nobody can account for is a support ticket.
            self.report.warn("zero_length", external_id=str(row.get("hourID")),
                             date=str(row.get("date"))[:10])
            return
        owner = resolver.user_by_ext.get(str(row.get("userID")))
        if owner is None:
            self.report.counts["skipped_user"] += 1
            self.report.warn("user_unmapped_hour", external_id=str(row.get("hourID")),
                             user=row.get("user"))
            return
        self.report.counts["pulled_new"] += 1
        if dry_run:
            return
        from app.modules.time.system import record_entry

        seconds = starts.get(int(row.get("hourID") or 0), 0)
        started = started_at_for(day, seconds)
        entry = await record_entry(
            self.ctx,
            user_id=owner,
            started_at=started,
            ended_at=started + timedelta(minutes=minutes),
            company_id=resolver.company_by_ext.get(str(row.get("customerID") or "")),
            project_id=resolver.project_by_ext.get(str(row.get("projectID") or "")),
            description=(row.get("remark") or "").strip() or None,
            # Stated explicitly, never left to the project default (#284): 314 of the
            # migration's entries disagree with their project's default and 783 have no project
            # at all, so deferring would silently re-decide a thousand rows in both directions.
            billable=bool(row.get("billable")),
        )
        link = await self._write_hour_link(
            entry=entry, row=row, starts=starts, resolver=resolver,
            origin=TimeonLinkOrigin.TIMEON,
        )
        await self.links.update(link, pulled_at=datetime.now(UTC))
        await self._reconcile_approval(link, entry, row, dry_run=False)

    async def _push_new(
        self, entry: TimeEntry, resolver: Resolver, *, dry_run: bool
    ) -> None:
        """A schakl entry Timeon has never seen. Create it there, or say why not."""
        user_ext = resolver.ext_by_user.get(entry.user_id)
        if user_ext is None:
            self.report.counts["skipped_user"] += 1
            self.report.warn("user_unmapped_local", entry_id=str(entry.id))
            return
        if entry.minutes <= 0:
            return
        self.report.counts["pushed_new"] += 1
        if dry_run:
            return
        try:
            saved = await self._save_remote(entry, resolver, observed={}, hour_id=None)
        except TimeonError as exc:
            self.report.error("push_failed", entry_id=str(entry.id), detail=str(exc)[:200])
            return
        hour_id = saved.get("hourID")
        if not hour_id:
            self.report.error("push_no_id", entry_id=str(entry.id))
            return
        await self._write_hour_link(
            entry=entry, row=saved, starts={}, resolver=resolver,
            origin=TimeonLinkOrigin.SCHAKL,
        )

    async def _local_gone(
        self, link: TimeonLink, row: dict[str, Any], *, may_push: bool, dry_run: bool
    ) -> None:
        """The schakl entry behind a pairing is gone.

        Under a direction that lets schakl write, that is an instruction: delete the Timeon row
        too — **softly**, so it lands in Timeon's own bin and a mistake is undoable there. Under
        a pull-only direction it is not an instruction at all, and the pairing is simply dropped
        so the next run treats the remote row as new.
        """
        if not may_push:
            self.report.counts["unpaired_local_gone"] += 1
            if not dry_run:
                await self.links.delete(link)
            return
        if row.get("invoiceID"):
            self.report.warn("remote_invoiced", external_id=link.external_id)
            return
        self.report.counts["deleted_remote"] += 1
        if dry_run:
            return
        try:
            await self.client.delete_hour(int(link.external_id))
        except TimeonError as exc:
            self.report.error("delete_failed", external_id=link.external_id,
                              detail=str(exc)[:200])
            await self.links.update(
                link, status=TimeonLinkStatus.ERROR.value, last_error=str(exc)[:500]
            )
            return
        await self.links.delete(link)

    async def _handle_vanished(
        self,
        vanished: list[TimeonLink],
        entries: dict[uuid.UUID, TimeEntry],
        *,
        may_pull: bool,
        dry_run: bool,
    ) -> None:
        """A pairing whose Timeon row is not in the window we just read (rule 3).

        Asked again by id first, because an hour moved to another month is absent here and is
        not gone. Only a row Timeon cannot find at all is treated as deleted, and even then a
        protected entry is reported rather than removed — an invoiced hour does not stop having
        happened because somebody tidied up in the other system.
        """
        if not vanished:
            return
        ids = [int(link.external_id) for link in vanished if link.external_id.isdigit()]
        try:
            still_there = {
                str(r.get("hourID")) for r in await self.client.hours_by_id(ids)
            }
        except TimeonError as exc:
            # Not knowing is a reason to do nothing, never a reason to delete.
            self.report.error("recheck_failed", detail=str(exc)[:200])
            return
        for link in vanished:
            if link.external_id in still_there:
                self.report.counts["moved_out_of_window"] += 1
                continue
            entry = entries.get(link.local_id) if link.local_id else None
            if entry is None:
                self.report.counts["pairs_dropped"] += 1
                if not dry_run:
                    await self.links.delete(link)
                continue
            if not may_pull:
                self.report.counts["remote_missing"] += 1
                if not dry_run:
                    await self.links.update(link, status=TimeonLinkStatus.MISSING.value)
                continue
            blocked = self._protection(entry)
            if blocked:
                self.report.counts[f"protected_{blocked}"] += 1
                self.report.warn("protected_delete", reason=blocked, entry_id=str(entry.id))
                if not dry_run:
                    await self.links.update(link, status=TimeonLinkStatus.MISSING.value)
                continue
            self.report.counts["deleted_local"] += 1
            if dry_run:
                continue
            from app.modules.time.system import remove_entry

            await remove_entry(self.ctx, entry)
            await self.links.delete(link)

    # --- approvals ------------------------------------------------------------ #
    async def _reconcile_approval(
        self, link: TimeonLink, entry: TimeEntry, row: dict[str, Any], *, dry_run: bool
    ) -> None:
        """Sign-off, which travels on its own switch.

        Separate from ``hours_direction`` because approving is a different act from logging: an
        agency may want its sign-offs to reach Timeon while the hours themselves only ever come
        the other way. Pulling an approval is always allowed under a pull direction and costs
        nothing — it is the one field where the two systems mean exactly the same thing.

        ``approved_by_user_id`` follows only where Timeon's approver is a paired schakl user; an
        unmapped approver leaves the *fact* of approval and drops the name, because a signature
        attributed to the wrong person is worse than an unattributed one.
        """
        direction = self._direction(self.account.hours_direction)
        remote_approved = bool(row.get("approved"))
        local_approved = entry.approved_at is not None
        if remote_approved == local_approved:
            return
        if remote_approved and direction in ("pull", "two_way"):
            self.report.counts["approvals_pulled"] += 1
            if dry_run:
                return
            resolver = await self.resolver()
            approver = resolver.user_by_ext.get(str(row.get("approvedBy") or ""))
            entry.approved_at = parse_timeon_ts(row.get("approvedOn")) or datetime.now(UTC)
            entry.approved_by_user_id = approver
            await self.ctx.session.flush()
            return
        if local_approved and self.account.push_approvals:
            self.report.counts["approvals_pushed"] += 1
            if dry_run:
                return
            try:
                await self.client.approve_hours([int(link.external_id)], approved=True)
            except TimeonError as exc:
                self.report.error("approve_failed", external_id=link.external_id,
                                  detail=str(exc)[:200])

    # --- helpers -------------------------------------------------------------- #
    def _direction(self, value: str) -> str:
        return SyncDirection(value).value

    def _protection(self, entry: TimeEntry) -> str | None:
        """Why this entry may not be rewritten from Timeon, or ``None``.

        Rule 5, and the reason it is a *string* rather than a boolean: "we did not touch this
        because it is on an invoice" and "…because it is approved" are two different sentences
        for the person reading the run report, and one flag makes both of them "protected".
        """
        if self.account.protect_invoiced and entry.invoiced_at is not None:
            return "invoiced"
        if self.account.protect_approved and entry.approved_at is not None:
            return "approved"
        return None

    @staticmethod
    def _reference_out(
        local_id: uuid.UUID | None,
        ext_by_local: dict[uuid.UUID, str],
        local_by_ext: dict[str, uuid.UUID],
        observed: dict[str, Any],
        field: str,
    ) -> str | None:
        """What a push should say about one reference — and the answer is not always "what we
        have".

        Three cases, and only the middle one is obvious. A **paired** local reference travels as
        its Timeon id. A **cleared** one — the entry has none, and the Timeon row's id *is*
        pairable — travels as ``None``, because somebody deliberately detached it here.

        The third is the trap the wholesale PUT sets. The entry has no reference and the Timeon
        row points at something schakl has never paired: an unmatched project, a client outside
        this org's register. schakl cannot *express* that reference, so it must not assert its
        absence — sending ``None`` would detach a client's hour from its project as a side effect
        of correcting a description, and nothing on either screen would say why. Carried, not
        authored, exactly as ``distance`` and the expense fields are.
        """
        if local_id is not None:
            return ext_by_local.get(local_id)
        current = observed.get(field)
        if current in (None, ""):
            return None
        return None if str(current) in local_by_ext else str(current)

    async def _save_remote(
        self,
        entry: TimeEntry,
        resolver: Resolver,
        *,
        observed: dict[str, Any],
        hour_id: int | None,
    ) -> dict[str, Any]:
        """Write one entry to Timeon, whole (rule 7)."""
        user_ext = resolver.ext_by_user.get(entry.user_id)
        if user_ext is None:
            raise TimeonError("no Timeon user paired with this entry's owner")
        started = entry.started_at.astimezone(UTC)
        payload = timeon_payload(
            hour_id=hour_id,
            observed=observed or {},
            user_ext=user_ext,
            company_ext=self._reference_out(
                entry.company_id, resolver.ext_by_company, resolver.company_by_ext,
                observed, "customerID",
            ),
            project_ext=self._reference_out(
                entry.project_id, resolver.ext_by_project, resolver.project_by_ext,
                observed, "projectID",
            ),
            day=started.date(),
            start_seconds=start_seconds_of(started),
            minutes=entry.minutes,
            description=entry.description,
            billable=entry.billable,
        )
        return await self.client.save_hour(payload)

    async def _write_hour_link(
        self,
        *,
        entry: TimeEntry,
        row: dict[str, Any],
        starts: dict[int, int],
        resolver: Resolver,
        origin: TimeonLinkOrigin,
    ) -> TimeonLink:
        """Create or refresh the pairing, with both fingerprints taken from the same instant.

        Both, always, and from the values just written on each side. A link whose two hashes were
        computed at different moments would report the gap between them as somebody's edit on the
        very next run.
        """
        has_start = row.get("fromSeconds") is not None
        remote = neutral_from_row(
            row, start_seconds=starts.get(int(row.get("hourID") or 0)), resolver=resolver
        )
        local = neutral_from_entry(entry, resolver=resolver, has_remote_start=has_start)
        ext = str(row.get("hourID"))
        now = datetime.now(UTC)
        existing = (
            await self.ctx.session.execute(
                self.links.scoped_select()
                .where(TimeonLink.account_id == self.account.id)
                .where(TimeonLink.kind == TimeonLinkKind.HOUR.value)
                .where(TimeonLink.external_id == ext)
                .limit(1)
            )
        ).scalars().first()
        values = {
            "local_id": entry.id,
            "company_id": entry.company_id,
            "external_name": (row.get("remark") or "")[:255] or None,
            "external_date": row_date(row),
            "status": TimeonLinkStatus.LINKED.value,
            "origin": origin.value,
            "local_hash": fingerprint(local),
            "remote_hash": fingerprint(remote),
            "observed": observed_of(row),
            "observed_at": now,
            "last_error": None,
        }
        if existing is not None:
            return await self.links.update(existing, **values)
        return await self.links.create(
            account_id=self.account.id,
            kind=TimeonLinkKind.HOUR.value,
            external_id=ext,
            **values,
        )

    async def _settle(
        self,
        link: TimeonLink,
        entry: TimeEntry,
        row: dict[str, Any],
        starts: dict[int, int],
        resolver: Resolver,
        *,
        pulled: bool = False,
        pushed: bool = False,
    ) -> None:
        """After a write, both sides agree — so both fingerprints are re-taken together."""
        link = await self._write_hour_link(
            entry=entry, row=row, starts=starts, resolver=resolver,
            origin=TimeonLinkOrigin(link.origin),
        )
        stamps: dict[str, Any] = {}
        if pulled:
            stamps["pulled_at"] = datetime.now(UTC)
        if pushed:
            stamps["pushed_at"] = datetime.now(UTC)
        if stamps:
            await self.links.update(link, **stamps)

    async def _upsert_link(
        self,
        kind: TimeonLinkKind,
        *,
        external_id: str,
        local_id: uuid.UUID,
        external_name: str | None,
        dry_run: bool,
        existing: TimeonLink | None,
        company_id: uuid.UUID | None = None,
    ) -> None:
        """A reference pairing (user / customer / project). No fingerprints: nothing about these
        is ever written by the sync, so "did it change" is not a question with consequences.

        Recorded on :attr:`_pairs` **before** the dry-run guard, so a dry run resolves ids exactly
        as the real run would — see :meth:`resolver`.
        """
        self._pairs.setdefault(kind.value, {})[external_id] = local_id
        if existing is not None and existing.local_id == local_id:
            if not dry_run and existing.external_name != external_name:
                await self.links.update(existing, external_name=external_name)
            return
        self.report.counts[f"{kind.value}_paired"] += 1
        if dry_run:
            return
        if existing is not None:
            await self.links.update(
                existing,
                local_id=local_id,
                company_id=company_id,
                external_name=external_name,
                status=TimeonLinkStatus.LINKED.value,
            )
            return
        await self.links.create(
            account_id=self.account.id,
            kind=kind.value,
            external_id=external_id,
            local_id=local_id,
            company_id=company_id,
            external_name=external_name,
            status=TimeonLinkStatus.LINKED.value,
            origin=TimeonLinkOrigin.TIMEON.value,
        )
