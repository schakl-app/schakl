# Timeon

> Two things live here. **§1–§3** are the one-way importer that moved breik. off Timeon in August
> 2026 — history, and kept because §2 is the argument this module had to answer. **§4 onwards** is
> `timeon`, the two-way sync integration that replaced it.
>
> Read §2 and §5 before changing anything. Everything else follows from them.

---

## Part one — the migration (2026-08-15)

### 1. What ran, and what it produced

`apps/api/scripts/timeon_import.py` (branch `timeon-import`), executed against production
(`breik` on `schakl.cloud`) after a verified backup.

| | Before | Imported | After |
|---|---|---|---|
| Companies | 108 | — | 108 |
| Projects | 8 | 157 | 165 (91 archived, 127 with budgets) |
| Time entries | 8 | 2814 | 2822 (2024-01-06 → 2026-08-15) |
| Users | 7 rows / 5 members | 3 accounts + 1 membership | 10 rows / 9 members |

Per-user hour totals match Timeon to the minute. 2622 entries carry their real approver.

**Clients were not imported.** All 108 Timeon customers already existed as schakl companies and
join 1:1 on `customerNumber` ↔ `client_number` — unique, no misses, no fuzzy matching. Do not be
tempted to match on name: *Maatschap Mini Camping Boudewijnskerke* exists twice in **both**
systems (402148 / 402149), so a name match is ambiguous exactly where it looks safest. Timeon's
`externalID` holds UUIDs from some earlier system; none resolve here. Ignore it.

### 2. Why the importer was not a sync — and why the sync exists anyway

The original ask was "sync or import?", and the answer given was: **an import**, on the ground
that a sync is worth its cost only while both systems stay authoritative. Timeon was being
retired, so a sync bought a permanent two-writer problem to cross a bridge once. The decisive
case was invoicing: `TimeEntry.invoiced_at` is a downstream fact, and once hours are on a
client's invoice the entry is a **record**, not live data. A sync means the other system can
rewrite the basis of an invoice already sent, which no reconcile can repair.

That argument was right about invoices and wrong about the bridge. **A cutover that takes months
is not a bridge; it is two systems both being used.** Between the import and the day Timeon is
switched off, people log hours there and correct them here, and a re-runnable importer loses one
of the two directions by construction: it can only ever carry Timeon's version, and it carries it
by *creating*, so a correction made in schakl is either overwritten or duplicated.

So the invoicing argument becomes a **mechanism** rather than a veto:

- `protect_invoiced` (on by default) refuses, per entry, to let anything rewrite or delete what
  has already reached a client's invoice. The divergence is reported instead.
- `history_floor` keeps the whole imported past out of reach, so the 2814 entries above are never
  re-read, re-priced or re-proposed.

Everything else §5–§7 of the old document said stays true and is implemented in the module: the
natural key that recognises what the importer already wrote, the per-employee ownership no REST
call can express, and the break field that is not a break.

### 3. Two things the data said that the brief did not

**Timeon stopped invoicing in April 2025.** Its invoice module ran Jan 2024 → Apr 2025 (228
invoices) and then nothing. So `invoiceID` is trustworthy for 2024 and meaningless after: 421
entries (758:24) in 2025 and 564 (917:07) in 2026 were approved, billable and carried no invoice
— which is schakl's exact `te factureren` predicate. Importing the flags literally would have
opened the new system with ~1675 hours of phantom backlog for work billed elsewhere. **Owner
decision: all imported history counts as billed.** `invoiced_at` is stamped on every *billable*
entry; a non-billable one is left alone, since it can never enter the backlog anyway.

**A third of the hours belonged to people with no schakl account.** Four of seven Timeon users
(916 entries, 32%) — and the REST API cannot help, because `TimeService.create` ends in
`repo.create(user_id=self.ctx.user.id, …)` with no override. Any HTTP-driven import files
everything under one account and destroys per-employee reporting. The sync inherits the problem
and the answer: it writes through `app/modules/time/system.py`, which takes a `user_id`.

---

## Part two — the sync (`app/integrations/timeon/`)

### 4. What it is

A **licensed integration** (§6a): it holds a credential for somebody else's service, and what it
stores is a *pointer into* state that lives over there. Switch Timeon off tomorrow and this module
is gone; `time` is merely poorer by one source. `requires=("time",)` and nothing else — pairing
projects is better with the `projects` module and is not impossible without it (an hour books onto
a client), and over-declaring makes a tenant switch on a module they did not want.

Four tables, and the split between them is the design:

| | what it is |
|---|---|
| `timeon_accounts` | a credential **and a policy** — direction per entity, how far back, what may never be touched, what happens when both sides changed |
| `timeon_links` | one pairing, carrying **two fingerprints** (`local_hash`, `remote_hash`) rather than a `synced` flag |
| `timeon_conflicts` | a *stored decision*, so the same divergence is never re-proposed |
| `timeon_sync_runs` | what a run did, what it refused, and **what window it did not look at** |

### 5. Nine rules

These are `sync.py`'s own numbering; each is here because getting it wrong is expensive.

1. **Adoption before creation, always.** The first run against an instance that already holds
   Timeon history — which is every instance, because §1 wrote 2814 entries here — must
   *recognise* those entries and pair them without writing a byte. It matches on the importer's
   own natural key, which is why `mapping.natural_key` is byte-identical to the one in
   `timeon_import.py`. A sync that created before it adopted would double three years of
   somebody's timesheet on its first press, and no undo exists for that. `kind="adopt"` is that
   phase on its own, and it is the button an agency presses on day one.
2. **A window is the sync.** Timeon's hour rows carry no modified timestamp (§7), so "what
   changed since last night" is not a question its API can answer. The run re-reads a date window
   and compares fingerprints. That makes the window a real horizon — so it is stored on the run,
   shown on the screen, and never implied.
3. **Absence is a deletion only inside a window we know we read completely, and only after asking
   again.** `filter.deleted` is accepted and *ignored* by Timeon, so a delete has no signal but
   absence. Two guards: `TimeonClient.hours` refuses a window whose row count disagrees with the
   server's own `summary.totalItems`, and a row that has vanished is re-read **by id** before
   anything is deleted — an hour moved from 5 August to 5 January is absent from August and is
   not gone.
4. **Both sides moved is a conflict, and a conflict is a stored decision.** Not a recomputation:
   a queue that re-proposes the same twelve rows every night is one nobody reads by the third
   week (#318). "Mag verschillen" is a real resolution and is recorded like the other two — which
   is precisely why nothing else may land in that arm, and the first run against the live
   organisation put 62 of 66 rows there. `UNRESOLVED` is a *sentinel*, and the fingerprint hashed
   it as though it were a value: a Timeon project with no pairing here canonicalises to `"?"`
   while the entry the pull had **just written** carries no project at all and canonicalises to
   `""`. Neither hash had moved since the two sides last disagreed, so every one of those rows was
   filed as a difference somebody had deliberately kept. Two rules come out of it. **Agreement is
   `mapping.differences`, never equality of the two fingerprints** — the hashes answer "did *this*
   side move", a strictly one-sided question, and only a pairwise test can know a field is
   unactionable on both sides at once. And **a difference no direction of sync could act on is not
   a difference**: a pull cannot set a project schakl has never heard of and a push cannot name one
   Timeon has never heard of, so it is reported once per run as `project_unmapped` — the form an
   admin can actually close — and the row itself says nothing. The moment the pairing appears the
   sentinel becomes a real id, that side reads as changed on the next run, and the value carries
   across on its own.
5. **An invoiced entry is a record, not live data.** §2's argument, made structural.
6. **An unmapped person is reported, never guessed.**
7. **A push sends the whole row.** `hour/save` replaces rather than patches (§7). Fields schakl
   has no concept of — distance, expenses, the category — are carried over from what was last
   observed, or a description correction would delete a client's mileage claim. A reference schakl
   cannot *express* (an unpaired project) is carried too; one it can and does not have is cleared,
   because that is somebody's deliberate act.
8. **A row-level refusal is reported; a call-level failure stops the run.** §18's split. One
   protected entry is one line in the report; a credential that stopped working is one message and
   a run that is not `ok`.
9. **A dry run is the default**, at the API and on the screen. Every counter is computed and
   nothing is written, so an agency can see exactly what turning this on would do (#305).

### 6. What is configurable, and why each one is a setting

| setting | default | why it is not decided in code |
|---|---|---|
| `hours_direction` / `projects_direction` | `off` | the honest answer usually differs per kind: an agency mid-migration pulls hours (people still log there) while pushing projects (they are set up here now) |
| `conflict_policy` | `manual` | `schakl_wins` / `timeon_wins` are a decision to overwrite somebody's edit; real, and chosen rather than inferred |
| `window_days` | 45 | long enough to catch a correction made while preparing last month's invoice, short enough that a nightly run reads two months rather than three years |
| `history_floor` | `NULL` | set it to the import date and §1's 2814 entries are permanently out of reach |
| `protect_invoiced` | on | §2 |
| `protect_approved` | off | an approval correction arriving from Timeon is ordinary mid-migration |
| `push_approvals` | off | approving is a different act from logging |
| `create_missing_projects` | off | a project is a thing an agency names deliberately; a sync inventing 157 is a mess to undo |
| `create_missing_users` | off | an account is a person, a membership may cost a seat, and the alternative failure ("3 people's hours were skipped") is loud and harmless |
| `auto_sync` | off | a scheduled job that started the moment a key was pasted would make connecting an irreversible act |
| `auto_frequency` · `auto_interval_hours` · `auto_time` | `daily` at `04:20` | §6a |

### 6a. The schedule is the tenant's, and it is visible (#387, #388)

Auto-sync used to be one boolean and one constant: `cron(timeon_nightly, hour=4, minute=20)` —
**04:20 UTC**, identical for every account on every instance, stated to a user only as the words
"Rond 04:20" in a help text. Two things were wrong with that and they compounded.

**A cadence is an operational choice.** During a cutover both systems are written to all day, so
how often the two are reconciled decides how large the two-writer window gets — hourly while
people log hours in both places, nightly once the traffic is one way again, and possibly one of
each for an agency running two Timeon organisations. So the ARQ cron became the **tick** (every
quarter of an hour) and the account became the **schedule**: `off` is still `auto_sync`, and
*when* is `auto_frequency` (`hourly` / `every_n_hours` / `daily` / `weekdays`), `auto_time` and
`auto_interval_hours`. `auto_time` is a **local wall clock in the org's zone** (§8), which is the
one deliberate behaviour change: 04:20 UTC is 06:20 in Amsterdam in summer and 05:20 in winter, so
the "nightly" moved by an hour twice a year on the only clock the tenant has.

Two consequences worth stating. **A sub-daily cadence shortens its window rather than re-reading
the same span twenty-four times a day** — the account's `window_days` is the right horizon for a
run that happens once a day and a rate limit spent on nothing for one that happens hourly, so an
interval connection reads `CATCH_UP_WINDOW_DAYS` on most ticks and its full window on the first
run of the org's local day. And **a time of day is honoured to within one tick, never to the
minute**: the tick is the clock's resolution, and saying so is cheaper than pretending otherwise.

**A schedule you cannot see is one you cannot trust**, and this one was not running at all. On
production, breik.'s connection had `auto_sync = true`, both directions `two_way`, and had
produced **no** cron run since it was connected — five nights, no failed run, no empty run,
nothing on any screen. The job returned in twenty milliseconds because of one call:

```python
if not await sku_writable("timeon"):   # no plan, no host
    return
```

With neither argument that reads the **instance licence**, which is the one authority a *cloud*
tenant does not have: the operator runs the installation and the tenant buys a plan
(`core/entitlements`). An org on `unlimited` — entitled to everything — was refused by a gate that
never got to ask about it. `timeon` was the only cron in the codebase with that shape; every other
one calls `sku_cron_enabled`, which answers from the licence self-hosted and defers to
`run_per_org` on cloud, where the per-org plan filter already lives. That is now what this one
calls, and the self-hosted refusal is unchanged.

The second half of that bug is the reason it survived: **a job that decides not to run leaves no
trace.** So `last_auto_run_at` is stored, the next run is computed by the same function the worker
decides with (`schedule.py` — two copies of a schedule rule is how a screen comes to promise a run
the worker does not make), and both are printed on Instellingen → Timeon *and* on the workspace,
beside a sentence naming the cadence and the zone. A refusal at the licence gate now says so in
the log naming the sku.

One more thing the fix carried: the per-account loop **commits per account**. The old rollback that
contained one connection's failure also discarded the hours the previous connection had just
synced, which is the opposite of the property the file's own docstring claimed. The RLS GUC is
transaction-local, so it is re-bound after every commit.

### 7. What the Timeon API actually does

None of this is in its OpenAPI document, which describes **no response bodies at all** — and no
request body for `/api/hour/save`, the one endpoint that writes. Every shape below came from a
live call; `apps/api/app/integrations/timeon/client.py` numbers them 1–7 and
`tests/timeon_fake.py` reproduces four of them so a regression fails here rather than in
production.

- **Auth**: the API key is not a bearer token. `POST /token?grant_type=apitoken&token=<key>` with
  an explicit `Content-Length: 0` — without it, HTTP **411**. Four-hour token, and the
  `refresh_token` in the response is null for this grant, so a long run re-exchanges on 401.
- **Cloudflare fronts the API and blocks the default Python user agent** with HTTP 403 and a body
  of `error code: 1010` — indistinguishable from a permissions failure. `curl` is unaffected, so a
  successful curl probe proves nothing about a Python client.
- **`hour/save` is a wholesale PUT.** Saving `{hourID, seconds}` and nothing else clears the
  remark and nulls out both `projectID` and `customerID`. Measured, twice, on rows created for the
  purpose.
- **An hour row has `createdOn` and no modified timestamp.** `billableModified` is a *boolean*
  ("somebody set this flag by hand"), not a date; a client reading it as a cursor would have one
  that is always false. `organisation` and each `user` do carry a real `modifiedOn`. Hours do not.
- **`filter.deleted` is accepted and ignored**: asking for deleted rows answers the live ones.
- **`showHidden` is a filter, not a widener**: `true` returns only hidden rows and `false` only
  visible ones. Omit it to get both (159 projects = 67 open + 92 closed).
- **`hour/list` is grouped by day** (`groups[].hourList[]`) and its `filter.paged` answers "not
  implemented". Pull by date range and assert each window's count against its own
  `summary.totalItems`.
- **Every response is HTTP 200**, refusals included: `{"success": false, "message": …}`.
- **`hour/approve` takes `hourIDs` as a comma-separated string**, the one place in this API a list
  is spelled that way.
- **Budgets are in seconds** (`budget.budget`; 302400 = 84:00).
- **`breakSeconds` is not a lunch break.** It is `(to − from) − seconds`, the unbooked remainder of
  the window — verified in 324 of the 325 rows that carry one, the exception being corrupt (a
  six-minute window reporting a 165-hour break). It is **dropped in both directions**.

### 8. What is not synced

Contacts, Timeon invoices as documents, tasks, categories, travel kilometres, rates, and
`secondsBillable` — Timeon can record 2 h worked against 1 h billed, and schakl has no separate
billable-duration field, so such an entry carries its full worked duration. Distance, expenses and
the category are **carried across a push** (rule 7) but never authored or read into schakl.

**Invoice *state* is guarded in both directions and carried in neither, and that is a decision
rather than an omission.** `protect_invoiced` refuses to let anything rewrite what schakl has
billed, and a Timeon row carrying an `invoiceID` blocks a push and is reported — but a *pulled*
hour never has `invoiced_at` set from it. Since `invoiced_at IS NULL` is exactly what puts an
hour on **Nog te factureren**, that would matter if an hour could still be invoiced in Timeon:
it would arrive here unbilled and be offered up a second time. It cannot. Invoicing moved to
schakl at the migration, the importer stamped every billable imported hour as invoiced so three
years of settled history can never reappear in that backlog, and `history_floor` keeps the sync
out of that past — so the only hours this integration pulls are ones schakl is going to invoice.
Owner's call, 2026-08-16, and the assumption it rests on is worth re-reading rather than
inheriting: **if anyone ever raises an invoice in Timeon again, this becomes a double-billing
hazard**, and the fix is to read `invoiceID` on the pull path — where the value is already in
`observed` and is already read for the push guard.

### 9. Turning it on, safely

1. Instellingen → Timeon → paste the API key. Both directions start `off`.
2. **Controleren** — it answers with the organisation's name and its size ("7 medewerkers, 108
   klanten, 159 projecten"). A key that merely works is not the answer; which books it opens is.
3. Set `history_floor` to the migration date if this instance was imported into.
4. `/timeon` → **Alleen koppelen**. It pairs what is already on both sides and writes nothing, so
   "2814 gekoppeld, 3 alleen in Timeon" is a fact to look at before deciding anything.
5. **Proefrun** with the direction you want. Read the report: what it would pull, push, protect
   and skip.
6. Only then **Synchroniseren**, and only then `auto_sync`.

**What has actually been proven against the live organisation, and what has not.** The read half
has: `verify`, the user/customer/project reads, adoption, a dry run and two real pull runs were
executed against breik.'s own Timeon account, and it is the *second* of those runs that found the
sentinel bug in rule 4 — the first looked perfect, because a first run's counters cannot tell an
idempotent sync from one that will re-decide every row forever. **Run it twice before believing
it.** The **push** half has only ever run against `tests/timeon_fake.py`. That is deliberate:
writing into an agency's live time registration is not something to try out, and `hour/save`
replaces rather than patches (rule 7), so a wrong body is a client's mileage claim deleted rather
than an error message. Before the first real push, do steps 4–5 with `hours_direction=push` on a
window containing **one** hour nobody minds, compare the row in Timeon's own UI field by field,
and only then widen the window. Everything the push path asserts about Timeon's behaviour comes
from measurement against the live API (§7), not from its documentation — but measurement of
*reads*.

### 9a. No card on the client hub, and it is a deliberate loss (#411)

`timeon.company` drew this client's Timeon identity, their paired-hour count and their open
conflicts on the company page. It is gone, and unlike the Ads and Tag Manager cards removed
beside it, **nothing takes its place** — so it is written down here rather than found later.

The reasoning is the one this whole page is about: a cutover ends. A card on every client's page
for a migration with a stated end date is a card that outlives its reason, and the questions it
answered are the ones somebody asks *while running a sync*, on `/timeon`, which is where the
answer can be acted on. The hub only ever said a conflict was waiting; the queue is where it is
settled. Everything the card read is still stored, still on `/timeon`, and still in the MCP
surface.

### 9b. Not in the main menu, and reachable from where the hours are (#389)

`timeon` was the only integration with a top-level nav entry — position 71, directly under Uren.
The argument for it is in this page: a two-way sync produces a queue, conflicts are settled by a
person, and a surface that has to be found is one that is not kept up to date. That is a good
argument for the queue being **reachable** and a poor one for a permanent menu slot, because of
what §10 says: a cutover ends. The day Timeon is switched off the entry points at an empty screen,
and until that day it is empty most days anyway — and a queue that shows nothing every day is
exactly the queue people stop reading. It is also wrong for a white-label platform, where every
other tenant of this codebase saw a vendor's name in their main menu for a product they have never
heard of.

So the workspace is reached the way every other integration's working surface is — from
**Instellingen → Integraties → Timeon**, which links straight through — and it *finds* the person
on the days it has something to say: `/time` draws an unsettled-conflict count beside the hours the
conflicts are about, non-zero only, gated on `timeon.sync.run` and read in the section layout so a
month of week clicks does not re-ask for it. `nav.timeon` survives as the page's breadcrumb label.

### 10. On cutover

Set both directions to `off` (or delete the connection — the pairings and runs go, the *hours*
stay), delete the API key from Timeon's side, and archive `apps/api/scripts/timeon_import.py`.
Part one of this page becomes history the day that happens; part two becomes it the day the
integration is removed from `settings.enabled_modules`.
