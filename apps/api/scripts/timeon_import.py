#!/usr/bin/env python3
"""One-way Timeon -> schakl importer (projects, budgets, time entries).

Run this **inside the API container**: it needs the app's service layer, because the REST API
deliberately cannot write another user's time entry (``TimeService.create`` ends in
``repo.create(user_id=self.ctx.user.id, ...)``) and cannot carry a historical approval.

It is **idempotent and re-runnable** — safe to run nightly until Timeon is switched off. There
is no state file and no schema change: an entry is matched on a natural key measured to be
unique for 2822 of 2823 real records, and the importer creates only the shortfall per key, so
the one genuine duplicate pair survives a re-run intact.

Nothing is written without ``--apply``.

**Read ``docs/TIMEON.md`` before running or changing this** — it holds the decisions this file
only implements (why an import and not a sync, why all history counts as billed), the Timeon
API's four undocumented behaviours, the backup recipe, and the failure modes a dry run cannot
find.

On the production box (Docker Swarm; resolve the container each time, and note that ``-w /app
-e PYTHONPATH=/app`` is required or ``import app`` fails)::

    C=$(docker ps -q -f name=schakl-cloud_api | head -1)
    docker cp /root/timeon_import.py "$C":/tmp/timeon_import.py
    run() { docker exec -w /app -e PYTHONPATH=/app \
              -e TIMEON_API_KEY="$(cat /root/.timeon_key)" "$C" \
              python /tmp/timeon_import.py --org breik "$@"; }

    run                     # dry run, writes nothing
    run --users --apply; run --projects --apply; run --hours --apply
    run --hours --apply     # must report 0 created
"""

from __future__ import annotations

import argparse
import asyncio
import calendar
import json
import logging
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger("timeon")

TIMEON_BASE = "https://api.timeon.nl"
# Cloudflare fronts this API and rejects `Python-urllib` outright with `error code: 1010`
# ("banned based on your browser's signature"), which reads exactly like an auth failure.
UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
FIRST_YEAR = 2024  # Timeon holds nothing before this
DEFAULT_START_SECONDS = 9 * 3600  # where a start-less entry's day begins


# --------------------------------------------------------------------------- Timeon client


class Timeon:
    """Minimal Timeon client.

    The API key is not a bearer token: it buys a 4-hour access token at ``/token`` and there is
    **no refresh token**, so a full-history run must be able to re-exchange mid-flight.
    """

    def __init__(self, api_key: str) -> None:
        self._key = api_key
        self._token: str | None = None

    def _exchange(self) -> None:
        qs = urllib.parse.urlencode({"grant_type": "apitoken", "token": self._key})
        req = urllib.request.Request(f"{TIMEON_BASE}/token?{qs}", data=b"", method="POST")
        req.add_header("User-Agent", UA)
        req.add_header("Content-Length", "0")
        with urllib.request.urlopen(req, timeout=60) as fh:
            body = json.loads(fh.read().decode())
        if not body.get("access_token"):
            raise RuntimeError(f"Timeon token exchange failed: {body.get('errorMessage')}")
        self._token = body["access_token"]

    def call(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        if self._token is None:
            self._exchange()
        for attempt in (1, 2):
            data = json.dumps(payload).encode() if payload is not None else None
            req = urllib.request.Request(
                TIMEON_BASE + path, data=data, method="POST" if payload is not None else "GET"
            )
            req.add_header("Authorization", f"Bearer {self._token}")
            req.add_header("User-Agent", UA)
            if payload is not None:
                req.add_header("Content-Type", "application/json")
            try:
                with urllib.request.urlopen(req, timeout=120) as fh:
                    return json.loads(fh.read().decode()).get("resultObject")
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 1:
                    self._exchange()  # the 4-hour token lapsed mid-run
                    continue
                raise
        raise RuntimeError("unreachable")

    def customers(self) -> list[dict[str, Any]]:
        return self._paged("/api/customer/list", {})

    def projects(self) -> list[dict[str, Any]]:
        # `showHidden` is a *filter*, not a widener: True returns only hidden rows and False only
        # visible ones. Omitting it entirely is the only way to get both.
        return self._paged("/api/project/list", {"calculateBudget": True})

    def _paged(self, path: str, extra: dict[str, Any]) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        page = 1
        while True:
            res = self.call(path, {"page": page, "pageSize": 100, **extra})
            out.extend(res["items"])
            if page >= res["nrPages"] or not res["items"]:
                return out
            page += 1

    def hours(self, upto: datetime) -> list[dict[str, Any]]:
        """Every hour row, pulled month by month.

        ``hour/list`` answers with day-groups and a cursor; its ``paged`` option is unimplemented.
        Each month's row count is asserted against the month's own ``summary.totalItems`` so a
        silently short answer can never be mistaken for a complete one.
        """
        out: list[dict[str, Any]] = []
        for year in range(FIRST_YEAR, upto.year + 1):
            for month in range(1, 13):
                if (year, month) > (upto.year, upto.month):
                    break
                last = calendar.monthrange(year, month)[1]
                window = {
                    "from": f"{year}-{month:02d}-01",
                    "to": f"{year}-{month:02d}-{last:02d}",
                }
                res = self.call("/api/hour/list", {"filter": window})
                rows = [h for g in (res.get("groups") or []) for h in (g.get("hourList") or [])]
                expected = (res.get("summary") or {}).get("totalItems", 0)
                if len(rows) != expected:
                    raise RuntimeError(
                        f"Timeon returned {len(rows)} rows for {year}-{month:02d} "
                        f"but reports {expected} — refusing a partial history."
                    )
                out.extend(rows)
        return out


# --------------------------------------------------------------------------- helpers


def natural_key(
    user_id: uuid.UUID, started_at: datetime, minutes: int, project_id: uuid.UUID | None, desc: str
) -> tuple[Any, ...]:
    """What makes an imported entry recognisably itself on a later run."""
    return (user_id, started_at.replace(tzinfo=None), minutes, project_id, (desc or "").strip())


def plan_start_times(hours: list[dict[str, Any]]) -> dict[int, int]:
    """Resolve a start-of-day second for every hour row, including the 605 with none.

    A start-less row is stacked after the previous one on that person's day from 09:00, so the
    timesheet reads plausibly instead of piling every such entry onto one instant. Placement
    depends only on the Timeon data and a stable sort, never on what is already in schakl — that
    is what keeps the natural key identical on a re-run.
    """
    placed: dict[int, int] = {}
    per_day: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in hours:
        per_day[(row["userID"], row["date"][:10])].append(row)
    for rows in per_day.values():
        cursor = DEFAULT_START_SECONDS
        for row in sorted(rows, key=lambda r: (r.get("fromSeconds") is None, r["hourID"])):
            if row.get("fromSeconds") is not None:
                placed[row["hourID"]] = int(row["fromSeconds"])
            else:
                placed[row["hourID"]] = cursor
                cursor += int(row.get("seconds") or 0)
    return placed


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.split(".")[0]).replace(tzinfo=UTC)


async def commit_rebind(session: Any, set_current_org: Any, org_id: uuid.UUID) -> None:
    """Commit, then re-bind the tenant.

    ``set_current_org`` writes the RLS GUC with ``set_config(..., true)`` — *transaction*-local.
    A commit therefore unbinds it and every later read fails closed to **zero rows** rather than
    erroring, so a multi-phase script silently stops seeing the tenant it just wrote to.
    """
    await session.commit()
    await set_current_org(session, org_id)


# --------------------------------------------------------------------------- the import


async def main() -> int:  # noqa: C901 - a migration reads better as one narrative
    ap = argparse.ArgumentParser(description="Import Timeon projects and hours into schakl.")
    ap.add_argument("--users", action="store_true", help="create the missing users (inactive)")
    ap.add_argument("--projects", action="store_true", help="upsert projects and their budgets")
    ap.add_argument("--hours", action="store_true", help="import time entries")
    ap.add_argument("--apply", action="store_true", help="actually write (default is a dry run)")
    ap.add_argument("--org", help="org slug to import into (required if the instance has several)")
    args = ap.parse_args()
    if not (args.users or args.projects or args.hours):
        args.users = args.projects = args.hours = True

    api_key = os.environ.get("TIMEON_API_KEY")
    if not api_key:
        print("TIMEON_API_KEY is not set", file=sys.stderr)
        return 2

    # Imported here so --help works outside the container.
    import importlib

    from sqlalchemy import select

    from app.config import settings

    # Load every enabled module exactly as `app.main` does at boot. Importing only the handful
    # of models this script names leaves the ORM registry incomplete, and SQLAlchemy then fails
    # to resolve unrelated foreign keys (`time_entries.subscription_id` -> `subscriptions`) on
    # the first flush — long after the run looked healthy.
    for _name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{_name}")

    from app.core.auth.models import User
    from app.core.models import Membership, Org
    from app.core.permissions.permset import PermissionSet
    from app.core.permissions.service import create_membership
    from app.core.tenancy import RequestContext
    from app.db import async_session_maker, set_current_org
    from app.modules.projects.models import Project, ProjectStatus
    from app.modules.projects.schemas import ProjectCreate
    from app.modules.projects.service import ProjectService
    from app.modules.time.models import TimeEntry
    from app.modules.time.schemas import TimeEntryCreate
    from app.modules.time.service import TimeService

    mode = "APPLY" if args.apply else "DRY RUN"
    print(f"\n=== Timeon -> schakl import [{mode}] ===\n")

    tio = Timeon(api_key)
    print("Reading Timeon ...")
    t_customers = tio.customers()
    t_projects = tio.projects()
    t_hours = tio.hours(datetime.now(UTC))
    print(
        f"  {len(t_customers)} customers, {len(t_projects)} projects, {len(t_hours)} hour rows\n"
    )

    async with async_session_maker() as session:
        # This runs on a `SCHAKL_DEPLOYMENT=cloud` instance, which can hold several tenants.
        # Guessing "the first org" would quietly import a client's history into the wrong one,
        # so a slug is required the moment there is more than one.
        orgs = (await session.execute(select(Org).order_by(Org.created_at))).scalars().all()
        if not orgs:
            print("no org in this instance", file=sys.stderr)
            return 2
        if args.org:
            org = next((o for o in orgs if o.slug == args.org), None)
            if org is None:
                print(f"no org with slug {args.org!r}; have "
                      f"{[o.slug for o in orgs]}", file=sys.stderr)
                return 2
        elif len(orgs) > 1:
            print(f"this instance has {len(orgs)} orgs {[o.slug for o in orgs]} — "
                  f"pass --org <slug>", file=sys.stderr)
            return 2
        else:
            org = orgs[0]
        await set_current_org(session, org.id)
        print(f"Org: {org.slug} ({org.id})\n")

        # ---------------------------------------------------------------- phase 1: users
        t_users = {u["userID"]: u for u in tio.call("/api/user/search", {})}
        rows = (await session.execute(select(User))).scalars().all()
        by_email = {u.email.lower(): u for u in rows}
        member_ids = {
            m.user_id for m in (await session.execute(select(Membership))).scalars().all()
        }
        # `users` is instance-level and `memberships` is per-org, so an account can exist while
        # belonging to no org — which is exactly renzo@breik.nl here. Matching on the user row
        # alone would call him "already present", skip him, and leave his 471 entries owned by
        # somebody who is not a member of the tenant.
        missing_user, missing_membership = [], []
        for user in t_users.values():
            email = (user.get("email") or "").lower()
            row = by_email.get(email)
            if row is None:
                missing_user.append(user)
            elif row.id not in member_ids:
                missing_membership.append((user, row))

        print(f"-- users: {len(t_users)} in Timeon, {len(missing_user)} missing in schakl, "
              f"{len(missing_membership)} present but not a member of {org.slug}")
        for user in missing_user:
            print(f"   + {user['email']:26} {user['name']!r} (new account, inactive)")
        for user, row in missing_membership:
            print(f"   + {user['email']:26} {user['name']!r} "
                  f"(existing account, active={row.is_active} — adding membership only)")

        if (missing_user or missing_membership) and args.users and args.apply:
            from pwdlib import PasswordHash

            hasher = PasswordHash.recommended()
            for user in missing_user:
                row = User(
                    id=uuid.uuid4(),
                    email=(user["email"] or "").lower(),
                    full_name=user.get("name"),
                    # Unusable password: these accounts exist to own history, not to log in.
                    hashed_password=hasher.hash(secrets.token_urlsafe(24)),
                    is_active=False,
                    is_verified=False,
                )
                session.add(row)
                await session.flush()
                await create_membership(session, org.id, row.id, "member")
                by_email[row.email] = row
            for _user, row in missing_membership:
                # Their `is_active` is left exactly as it is: this script is not the place to
                # flip an account somebody else deliberately enabled.
                await create_membership(session, org.id, row.id, "member")
            await commit_rebind(session, set_current_org, org.id)
            print(f"   created {len(missing_user)} account(s), "
                  f"{len(missing_membership)} membership(s)")
        print()

        # Map Timeon user -> schakl user for everything downstream.
        user_map: dict[int, User] = {}
        for tid, user in t_users.items():
            row = by_email.get((user.get("email") or "").lower())
            if row is not None:
                user_map[tid] = row
        unmapped = set(t_users) - set(user_map)
        if unmapped:
            print(f"   !! {len(unmapped)} Timeon user(s) still unmapped: "
                  f"{[t_users[i]['email'] for i in unmapped]}")
            print("      their hours cannot be imported — run --users --apply first\n")

        # ---------------------------------------------------------------- company mapping
        from app.modules.companies.models import Company

        comp_rows = (await session.execute(select(Company))).scalars().all()
        comp_by_number = {
            str(c.client_number).strip(): c for c in comp_rows if c.client_number
        }
        cust_by_id = {c["customerID"]: c for c in t_customers}

        def company_for(customer_id: int | None) -> Company | None:
            cust = cust_by_id.get(customer_id) if customer_id else None
            if cust is None:
                return None
            return comp_by_number.get(str(cust.get("customerNumber") or "").strip())

        unresolved_customers = [
            c for c in t_customers
            if str(c.get("customerNumber") or "").strip() not in comp_by_number
        ]
        print(f"-- clients: {len(t_customers)} in Timeon, "
              f"{len(t_customers) - len(unresolved_customers)} matched on client_number")
        for cust in unresolved_customers:
            print(f"   !! no schakl company for {cust['name']!r} (nr={cust.get('customerNumber')})")
        print()

        # ---------------------------------------------------------------- phase 2: projects
        # An owner-equivalent context: this script is the migration, not a user request.
        admin = next((u for u in rows if u.is_superuser), None) or rows[0]
        admin_membership = await session.scalar(
            select(Membership).where(Membership.org_id == org.id, Membership.user_id == admin.id)
        )
        admin_ctx = RequestContext(
            user=admin,
            org=org,
            session=session,
            membership_id=admin_membership.id if admin_membership else None,
            permissions=PermissionSet.of(["*"]),
        )

        existing_projects = (await session.execute(select(Project))).scalars().all()
        proj_by_key = {
            (p.company_id, (p.name or "").strip().lower()): p for p in existing_projects
        }
        project_map: dict[int, Project] = {}
        to_create: list[tuple[dict[str, Any], Any]] = []
        skipped_projects = 0

        for proj in t_projects:
            company = company_for(proj.get("customerID"))
            if company is None:
                skipped_projects += 1
                continue
            key = (company.id, (proj["name"] or "").strip().lower())
            hit = proj_by_key.get(key)
            if hit is not None:
                project_map[proj["projectID"]] = hit
            else:
                to_create.append((proj, company))

        archived = sum(1 for p, _ in to_create if p["statusID"] == 2)
        print(f"-- projects: {len(t_projects)} in Timeon")
        print(f"   {len(project_map)} already in schakl, {len(to_create)} to create "
              f"({archived} of them archived), {skipped_projects} unmappable")

        if args.projects and args.apply:
            service = ProjectService(admin_ctx)
            for proj, company in to_create:
                budget = (proj.get("budget") or {}).get("budget")
                created = await service.create(
                    ProjectCreate(
                        company_id=company.id,
                        name=proj["name"],
                        status=(
                            ProjectStatus.ARCHIVED if proj["statusID"] == 2
                            else ProjectStatus.ACTIVE
                        ),
                        billable_default=bool(proj.get("defaultBillable")),
                        # Timeon states a budget in seconds.
                        budget_hours=round(budget / 3600.0, 2) if budget else None,
                        budget_period="total",
                    )
                )
                project_map[proj["projectID"]] = created
            await commit_rebind(session, set_current_org, org.id)
            print(f"   created {len(to_create)} project(s)")
        elif to_create:
            print("   (dry run — not created; hours will not resolve a project until they are)")
        print()

        # ---------------------------------------------------------------- phase 3: hours
        starts = plan_start_times(t_hours)

        existing_entries = (await session.execute(select(TimeEntry))).scalars().all()
        have: Counter[tuple[Any, ...]] = Counter(
            natural_key(e.user_id, e.started_at, e.minutes, e.project_id, e.description or "")
            for e in existing_entries
        )
        print(f"-- hours: {len(t_hours)} in Timeon, "
              f"{len(existing_entries)} entries already in schakl")

        planned: list[tuple[dict[str, Any], dict[str, Any]]] = []
        skip_zero = skip_user = 0
        want: Counter[tuple[Any, ...]] = Counter()

        for row in sorted(t_hours, key=lambda r: (r["date"], r["hourID"])):
            seconds = int(row.get("seconds") or 0)
            if seconds <= 0:
                skip_zero += 1
                continue
            owner = user_map.get(row["userID"])
            if owner is None:
                skip_user += 1
                continue
            company = company_for(row.get("customerID"))
            project = project_map.get(row.get("projectID")) if row.get("projectID") else None
            day = datetime.fromisoformat(row["date"].split("T")[0])
            # A time entry's clock is the wall clock the user typed, stored as UTC (CLAUDE.md §8).
            # Converting Amsterdam->UTC here would shift every historical timesheet by an hour.
            started = datetime(
                day.year, day.month, day.day, tzinfo=UTC
            ) + timedelta(seconds=starts[row["hourID"]])
            minutes = seconds // 60
            desc = (row.get("remark") or "").strip() or None
            key = natural_key(
                owner.id, started, minutes, project.id if project else None, desc or ""
            )
            want[key] += 1
            if want[key] <= have.get(key, 0):
                continue  # already imported on an earlier run
            planned.append(
                (
                    row,
                    {
                        "owner": owner,
                        "company_id": company.id if company else None,
                        "project_id": project.id if project else None,
                        "started_at": started,
                        "minutes": minutes,
                        # Timeon's `breakSeconds` is NOT a lunch break: it is the unbooked
                        # remainder of the from/to window, `(to - from) - seconds`, in 324 of
                        # the 325 rows that carry one (the odd one out is corrupt — a 6-minute
                        # window reporting a 165-hour break). Feeding a derived remainder into
                        # schakl's break field would push an entry's end hours past the work and
                        # blow the 24h cap; the worked duration below is the exact figure that
                        # billing, capacity and reporting all read.
                        "break_minutes": 0,
                        "billable": bool(row.get("billable")),
                        "description": desc,
                    },
                )
            )

        print(f"   {len(planned)} to create, {skip_zero} skipped (zero length), "
              f"{skip_user} skipped (unmapped user)")
        if skip_zero:
            print("   zero-length rows are listed below and need no action:")
            for row in t_hours:
                if not int(row.get("seconds") or 0):
                    print(f"      hourID={row['hourID']} {row['date'][:10]} "
                          f"{row.get('user')!r} {row.get('remark','')!r}")

        if args.hours and args.apply and planned:
            created = 0
            per_owner: dict[uuid.UUID, RequestContext] = {}
            for _row, spec in planned:
                owner = spec["owner"]
                ctx = per_owner.get(owner.id)
                if ctx is None:
                    membership = await session.scalar(
                        select(Membership).where(
                            Membership.org_id == org.id, Membership.user_id == owner.id
                        )
                    )
                    ctx = RequestContext(
                        user=owner,
                        org=org,
                        session=session,
                        membership_id=membership.id if membership else None,
                        permissions=PermissionSet.of(["*"]),
                    )
                    per_owner[owner.id] = ctx
                entry = await TimeService(ctx).create(
                    TimeEntryCreate(
                        company_id=spec["company_id"],
                        project_id=spec["project_id"],
                        description=spec["description"],
                        billable=spec["billable"],
                        break_minutes=spec["break_minutes"],
                        started_at=spec["started_at"],
                        minutes=spec["minutes"],
                    )
                )
                # The only columns written outside a service call. `set_approval` would stamp
                # *now* and fire a notification per owner for a three-year-old sign-off, and
                # `set_invoiced` would silently approve what Timeon left unapproved.
                if _row.get("approved"):
                    entry.approved_at = parse_ts(_row.get("approvedOn")) or spec["started_at"]
                    approver = user_map.get(_row.get("approvedBy"))
                    entry.approved_by_user_id = approver.id if approver else None
                # Decision: all imported history counts as already billed, so nothing Timeon
                # holds can ever reappear in schakl's "te factureren". Only a billable entry
                # can enter that backlog, so only a billable entry needs the mark.
                if spec["billable"]:
                    entry.invoiced_at = entry.approved_at or spec["started_at"]
                created += 1
                if created % 250 == 0:
                    await session.flush()
                    print(f"      ... {created}/{len(planned)}")
            await commit_rebind(session, set_current_org, org.id)
            print(f"   created {created} time entries")
        elif planned:
            print("   (dry run — nothing written)")
        print()

        # ---------------------------------------------------------------- warnings
        from app.modules.google.oauth import google_settings_row

        try:
            grow = await google_settings_row(session, org.id)
            if grow is not None and getattr(grow, "drive_enabled", False) and getattr(
                grow, "drive_auto_provision", False
            ):
                print("!! Google Drive auto-provisioning is ON: creating projects will queue a "
                      "Drive folder job for each one.")
        except Exception:  # noqa: BLE001 — a missing/renamed settings row must not stop a report
            pass

        # Entries typed straight into schakl during the overlap, which Timeon may also hold.
        # Anything whose natural key belongs to the Timeon set is *ours* — on a second run that
        # is every row we just wrote, so matching on the date alone would report the import
        # itself as a double-counting risk.
        overlap_from = datetime(FIRST_YEAR, 1, 1, tzinfo=UTC)
        foreign = [
            e
            for e in existing_entries
            if e.started_at >= overlap_from
            and natural_key(e.user_id, e.started_at, e.minutes, e.project_id, e.description or "")
            not in want
        ]
        if foreign:
            print(f"!! {len(foreign)} schakl entries in the imported period did not come from "
                  f"Timeon — review these by hand for double counting:")
            for e in foreign:
                desc = (e.description or "")[:50]
                print(f"     {e.started_at:%Y-%m-%d %H:%M}  {e.minutes:>4}m  {desc!r}")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
