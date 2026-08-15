# Timeon migration

> The one-way importer that moved breik.'s history off Timeon: 159 projects with their budgets
> and 2823 hour rows, from January 2024 to cutover. `apps/api/scripts/timeon_import.py`.
> Read this before re-running it, and before writing the next importer — most of what follows
> is not about Timeon.

Unlike every other document here this describes a **migration, not a module**. It has an end
date: when Timeon is switched off, the script and this page are history. What should outlive
both is §2 (why an import rather than a sync) and §7 (four failures that a dry run cannot find).

## 1. What ran, and what it produced

Executed against production (`breik` on `schakl.cloud`) on 2026-08-15, after a verified backup.

| | Before | Imported | After |
|---|---|---|---|
| Companies | 108 | — | 108 |
| Projects | 8 | 157 | 165 (91 archived, 127 with budgets) |
| Time entries | 8 | 2814 | 2822 (2024-01-06 → 2026-08-15) |
| Users | 7 rows / 5 members | 3 accounts + 1 membership | 10 rows / 9 members |

Per-user hour totals match Timeon to the minute. 2622 entries carry their real approver.
Te factureren reads **1 entry / 2:00** — Stan's own schakl entry of 4 August, which is correct:
the "already billed" decision covers imported history, not work logged natively.

**Clients were not imported.** All 108 Timeon customers already existed as schakl companies and
join 1:1 on `customerNumber` ↔ `client_number` — unique, no misses, no fuzzy matching. Do not
be tempted to match on name: *Maatschap Mini Camping Boudewijnskerke* exists twice in **both**
systems (402148 / 402149), so a name match is ambiguous exactly where it looks safest. Timeon's
`externalID` holds UUIDs from some earlier system; none resolve here. Ignore it.

## 2. Why an import and not a sync

The original ask was "sync or import?", and the answer is not about effort — a sync is roughly
one module's worth of work either way. It is about **who owns the truth**.

A sync is worth its cost only while both systems stay authoritative. Timeon is being retired, so
a sync buys a permanent two-writer problem to cross a bridge once. The decisive case is
invoicing: `TimeEntry.invoiced_at` is a downstream fact, and once hours are on a client's
invoice the entry is a **record**, not live data. A sync means the other system can rewrite the
basis of an invoice already sent, which no reconcile can repair. It is the rule
`docs/PAYMENTS.md` already states from the other end — gate what the agency *does*, never
rewrite what has already happened to them.

What people actually want from "sync" is *not losing the days between now and cutover*. That is
served by making the importer **re-runnable**, which costs nothing extra (§5), and it is why the
script is safe to run nightly until Timeon is off.

## 3. Two things the data said that the brief did not

Both were found by reading the live API before writing a line of the importer, and both would
have been expensive to discover afterwards.

**Timeon stopped invoicing in April 2025.** Its invoice module ran Jan 2024 → Apr 2025 (228
invoices) and then nothing. So `invoiceID` is trustworthy for 2024 and meaningless after: 421
entries (758:24) in 2025 and 564 (917:07) in 2026 were approved, billable and carried no
invoice — which is schakl's exact `te factureren` predicate. Importing the flags literally would
have opened the new system with ~1675 hours of phantom backlog for work that was billed
elsewhere. **Owner decision: all imported history counts as billed.** `invoiced_at` is stamped on
every *billable* entry; a non-billable one is left alone, since it can never enter the backlog
anyway and marking unsellable work "invoiced" would be a lie the data does not need.

**A third of the hours belonged to people with no schakl account.** Four of seven Timeon users
(916 entries, 32%) — and the REST API cannot help, because `TimeService.create` ends in
`repo.create(user_id=self.ctx.user.id, ...)` with no override, and the CSV path marks `user`,
`approved` and `invoiced` readonly on purpose. Any HTTP-driven import files everything under one
account and destroys per-employee reporting. Hence §4.

## 4. Why it runs inside the API

The script builds a `RequestContext` **per user** — the dataclass in `app/core/tenancy.py`, with
`PermissionSet.of(["*"])` and RLS bound through `set_current_org`, mirroring `run_per_org` in
`app/core/jobs.py` — and then calls the ordinary service. Every entry gets the validation,
ownership and billable resolution a form submit would (CLAUDE.md §17: an import is not a
backdoor around the service layer).

Three columns are written directly afterwards, and this is the only place that happens:
`approved_at`, `approved_by_user_id`, `invoiced_at`. The service methods that own them cannot
express history — `set_approval` stamps *now* and emits a notification per owner (2622
notifications about three-year-old sign-offs), and `set_invoiced` silently approves anything
Timeon left unapproved. The run report names this deviation rather than hiding it.

`billable` is passed **explicitly**, never left to the project default: 314 entries disagree
with their project's `billable_default` and 783 have no project at all, so deferring (#284)
would silently re-decide roughly a thousand rows in both directions.

## 5. Idempotency without a schema change

`TimeEntry` has no `custom` column, so an external-id column would mean a migration — and a
migration for a one-off import drags it into the release train for no lasting benefit.

Instead the key is natural and the check is stateless:
`(user, started_at, minutes, project, description)`. Measured against the real corpus this is
unique for **2822 of 2823** rows; the single collision is a pair of identical 2-hour no-remark
entries, and even that resolves because start-less entries are stacked deterministically (§6).
The importer counts how many entries already match each key and creates only the shortfall, so a
re-run cannot duplicate and there is no state file to lose.

**An existing schakl project or entry is reused, never overwritten.** That is what makes the
re-run safe, and it has a visible cost: two projects that already existed by hand kept their own
(absent) budgets — see §8.

## 6. What the Timeon API actually does

None of this is in its OpenAPI document, which describes **no response bodies at all**. Every
shape below came from a live call.

- **Auth**: the API key is not a bearer token. `POST /token?grant_type=apitoken&token=<KEY>`
  with a `Content-Length: 0` header — without it, HTTP 411. Returns a **4-hour** token and **no
  refresh token**, so a long run must re-exchange on 401. There is no client-credentials grant.
- **Cloudflare fronts the API and blocks `Python-urllib`** with HTTP 403 and a body of
  `error code: 1010` — indistinguishable from a permissions failure. Send a browser
  `User-Agent`. `curl` is unaffected, so a successful curl probe proves nothing about a Python
  client.
- **`showHidden` is a filter, not a widener**: `true` returns *only* hidden rows and `false`
  only visible ones. Omit it to get both (159 projects = 67 active + 92 closed).
- **`hour/list` is grouped by day** (`groups[].hourList[]`) with a `lastGroup` cursor;
  `filter.paged` answers "not implemented". Pull month by month and assert each month's row
  count against its own `summary.totalItems`, or a short answer passes for a complete one.
- **Budgets are in seconds** (`budget.budget`; 302400 = 84:00).
- **`breakSeconds` is not a lunch break.** It is `(to − from) − seconds`, the unbooked remainder
  of the window — verified in 324 of the 325 rows that carry one, the exception being corrupt
  (a six-minute window reporting a 165-hour break). Importing it would have pushed entry ends
  hours past the work and blown schakl's 24-hour cap, so **breaks are dropped** and the worked
  duration — the figure billing, capacity and reporting all read — is carried exactly.
- `summary.totalUnInvoiced` already means *approved AND billable AND not invoiced*, the same
  predicate as `te factureren`.

Data shape, for the record: 2823 rows, no expenses, 39 travel rows (which carry real time and
import normally, minus the kilometres), 0 deleted, 0 rejected, 8 zero-length rows (skipped and
listed — schakl rejects a zero span), 605 with no start time, no categories and no tasks in use.

## 7. Four failures a dry run cannot find

Worth reading before writing any out-of-band script against this codebase. Each was caught by
running for real — the first two against a throwaway database, the last two against production.

1. **RLS unbinds on commit.** `set_current_org` uses `set_config(..., true)` — *transaction*-
   local. Every `session.commit()` drops it and subsequent reads fail closed to **zero rows**
   without raising. A multi-phase script therefore commits phase 1 and then reports "0 of 108
   companies matched" for rows plainly present. Re-bind after every commit.
2. **An incomplete ORM registry fails late.** Importing only the models a script names leaves
   SQLAlchemy unable to resolve unrelated foreign keys, and it dies on the **first flush** —
   long after the read phase looked healthy — with `NoReferencedTableError` about a table the
   script never mentions. Replicate `main.py`'s `enabled_modules` import loop.
3. **`users` is instance-level; `memberships` is per-org.** An account can exist while belonging
   to no org, which is exactly what one active user turned out to be. Matching on the user row
   alone calls them "already present", skips them, and leaves their entries owned by a
   non-member. Handle *exists-but-not-a-member* as its own case — and leave `is_active` alone,
   because a script has no business flipping an account somebody deliberately enabled.
4. **The instance is `SCHAKL_DEPLOYMENT=cloud`**, so it can hold several tenants. "The first
   org" is a bug waiting for the second one; the script requires `--org` once more than one
   exists.

## 8. Running it, and what is left by hand

Production is `root@51.15.93.60` (Docker **Swarm**, Scaleway managed Postgres 17). Container
names carry a task suffix and change on every deploy, so resolve them each time; `-w /app
-e PYTHONPATH=/app` is required or `import app` fails.

```bash
C=$(docker ps -q -f name=schakl-cloud_api | head -1)
docker cp /root/timeon_import.py "$C":/tmp/timeon_import.py
run() { docker exec -w /app -e PYTHONPATH=/app \
          -e TIMEON_API_KEY="$(cat /root/.timeon_key)" "$C" \
          python /tmp/timeon_import.py --org breik "$@"; }

run                       # dry run — writes nothing
run --users --apply; run --projects --apply; run --hours --apply
run --hours --apply       # must report "0 to create"
```

**Backing up first is awkward and the recipe is not obvious**: there is no `pg_dump` on the host
or in the API image, and the app role has neither `SUPERUSER` nor `BYPASSRLS` (Scaleway keeps
`_rdb_superadmin`). A plain dump fails twice — server/client version mismatch, then `query would
be affected by row-level security policy`. What works is `postgres:17-alpine` with the org GUC
set and `--enable-row-security`; build the URL with `make_url(settings.database_url)` inside the
container, because the password contains characters that break `urllib.parse.urlsplit`. Since it
dumps only *visible* rows, it is a complete backup only while the instance has one org.

**Left for a human.** Two Timeon projects already existed in schakl and were reused (§5), so
they did not receive their Timeon budget: H2Booster `SEO 2026` (48 h) and Koelewijn B.V.
`Google ads` (36 h, and closed in Timeon while active here). Two rows, safer set by hand than by
a script that guesses which side is right.

**Not imported**: contacts, Timeon invoices as documents, tasks, categories, travel kilometres,
rates, and `secondsBillable` — Timeon can record 2 h worked against 1 h billed, and schakl has
no separate billable-duration field, so such an entry lands with its full worked duration. It
does not affect `te factureren` (everything imports as invoiced) but it means imported hours
reconcile against Timeon's *worked* totals, not its *revenue* totals.

**On cutover**: delete `/root/.timeon_key`, `/root/timeon_import.py` and `/root/timeon_hours.log`
from the server, and this page becomes an archive.
