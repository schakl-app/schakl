# Performance — a first-class requirement

> **Performance is incredibly important. A slow-feeling page is a bug, not a polish item.**
> Users notice slow navigation immediately (UX.md, Principle 2). Treat latency the way you
> treat correctness: budget it, measure it, and don't regress it. Read this before adding a
> screen, a load function, or a list endpoint.

## The one rule

**Before you write code for a page, count its API calls and DB queries.** Then remove every
one you don't need. Most slow pages are slow because they make calls they didn't have to.

## How the data path is shaped here

- **The web talks only to the API** (Golden Rule 6); every SSR render is a fan of HTTP calls.
  Fewer, parallel, lighter calls = a snappier page.
- **Never `await` calls in series when they're independent.** Use `Promise.all`. A sequential
  chain of N calls costs N round-trips; the same calls in parallel cost one.
- **Links preload on hover** (`data-sveltekit-preload-data="hover"`) so the next page's load
  starts before the click.

## Section layouts hold the lookups (hard rule)

**Every list section has a `+layout.server.ts`, and everything the URL does not change lives in
it.** A layout load does not rerun on filter, sort, page, tab or detail navigation — a page load
reruns on all of them. So a client picker fetched by the page is refetched on every keystroke of
a search box; the same picker fetched by the layout is fetched once per visit to the section.

Every section does this: companies, projects, contacts, interactions, subscriptions, domains,
websites, invoices, quotes, tasks, time, overview. Websites was the extreme case — twelve calls,
of which the URL changed exactly one, so sorting a column refetched eleven pickers.

Three rules that make it safe:

- **A layout load never `await`s `parent()` before starting its own flight.** Do that and the
  whole fan serialises behind the app layout for no reason. Where a *page* genuinely depends on
  the parent (the saved column layout decides the sort and whether an aggregate is worth
  computing), await it first — `event.parent()` is memoised, so the layout's calls are already
  in flight.
- **Freshness rides `invalidateAll`.** Every quick-create here is `use:enhance`d and a successful
  submit reruns layout loads too, so a client created inline appears in the picker that opened it.
  Nothing extra is needed.
- **Grep the sibling routes for key collisions first.** A page's data wins over its layout's, so
  hoisting a key a sibling tab returns in its own filtered form (subscriptions' `types` /
  `templates`) leaves that tab paying for a copy it discards. Leave those on the page.

**`svelte-check` does not catch a `data.x` in markup whose load stopped returning `x`.** After
moving keys between loads, cross-check by hand: for each `+page.svelte` under the section,
collect `data.<key>` and confirm each is produced by its own load or a layout above it.

## One flight per load; stream the rest

- **Never await the entity before an independent fan.** A detail page's other calls are keyed by
  the id in the URL, not by anything the row says, so `await entity` in front of them buys a
  round-trip of latency and nothing else. Put the entity *in* the fan and move the 404 check
  below it.
- **Modal-only payloads stream** (the `createForm` pattern): the contact list behind an edit
  dialog, a website form's hosting picker, the quick-create definitions. Return the promise
  unawaited and resolve it into `$state` — **not** a raw `{#await}` in the markup, which falls
  back to its pending branch on every invalidation and throws away what the user had typed. If an
  intent link (`?edit=1`) can open the modal before the data lands, render a pending state rather
  than an empty picker.
- **Report bodies stream behind the shell.** The filters, range picker and column menu do not need
  500 rows to render. Say "loading", not "no results", while it is in flight — the second is a
  wrong answer, not a slow one.
- **Nothing fetches on mount for a dropdown nobody opened.** `RichTextEditor` fetches its `@`/`#`
  candidates on first focus from a TTL cache (`lib/core/richtext/candidates.ts`); pass it a
  `scope`, never a pre-fetched list. Where several components on one page want the same browser-
  side lookup, cache the *promise* at module scope so they share one flight (safe in the browser,
  in one user's tab — the same cache on the server would be a tenant-isolation bug).

## A gesture repeated all day does not reload the page

`invalidateAll` is the right default for a write, and it is the wrong one for a *tick*. SvelteKit's
`update()` reruns every load above the form, so ticking one to-do on a task re-ran the two layouts
and the page — sixteen API calls, one of them the eight-round-trip `GET /tasks/{id}` — and the
checkbox did not change colour until all of it came back. The page was correct and the gesture felt
broken, which is the same bug.

Where a control is used dozens of times on one screen (a checklist tick, a complete-toggle, a
reorder), write it optimistically instead:

- **Flip the row the screen is drawing from, not the load's copy.** The task page's checkboxes,
  the "3/7" and the finish prompt all read the drag arrays, so one assignment moves all of them.
  If a derived count feeds a *decision*, derive it from the same array — counting the load's rows
  leaves the decision a round trip behind the click.
- **Take the new value from the serialised body** (`formData.get(...)` in the `use:enhance`
  callback), never from the state you are about to mutate: what the screen shows can then never
  disagree with what was sent.
- **`applyAction(result)`, never `update()`** — it surfaces the action's message and invalidates
  nothing. On anything but a success, put the row back: with no reload to correct it, an
  unreported refusal leaves the screen claiming a change the server never made, so the action
  must `fail()` with an error key rather than swallow the API error.
- **Say what the next load picks up.** Optimism is only safe because the *rest* of what the write
  touched (here, the activity line) is not on screen or is allowed to lag.

The endpoint behind such a control is a budget: a tick must cost the same on a checklist of eleven
as on a checklist of one (`test_perf_query_budgets.py`).

## A list row carries only what the list draws

Opt-outs exist on the list endpoints for exactly this — **use them wherever the extra work is
discarded**, and **add one** when you find a list shipping something its screen never renders:

- **`count=false`** — skip the `SELECT count(*)` that computes `Page.total`. Pass it from
  name-only lookups and pickers and any widget that never shows a total (companies, projects,
  tasks, time entries, contacts, notifications, interactions). `total` then returns the page
  length. On interactions it skips a `count(DISTINCT …)` over the conversation fold — a second
  full pass over the filter, not a by-product.
- **`meta=false`** (tasks) — skip the per-task label/checklist/comment aggregate subqueries.
  Pass it whenever you only need id/title/status/dates (grouping, pickers, the timesheet
  lookups). **Not** gated on column visibility: `TaskRow` draws those badges in its primary
  cell whatever the columns say, so a column-driven gate would silently remove them on mobile.
- **`with_body`** (interactions, default **off**) — a list row's `body_text` is a full e-mail
  body. The key stays in the payload as `null`; the detail view fetches the row it opens.
- **`lines=false`** (invoices, quotes) — the index draws number, client, date, status and total,
  never a line. `total`/`outstanding`/`overdue` are columns and still answer.

Two rules behind those: **don't fetch heavy aggregates to render a label**, and **don't request
200 rows to show 5** — sort and cut server-side instead (`/tasks/dashboard-groups`,
`/projects/dashboard-budgets`).

**A payload the form posts back is not optional.** Dropping `body_text` from interaction list
rows meant the edit form had to fetch the row *before* opening, or a save would have written an
empty body over the notes. When you slim a list, check who *writes* those fields back.

## Aggregate in SQL, never in Python

Loading rows to `sum()` them is a bug, not a style. `TimeService.logged()` selected every entry
a client had ever booked to add up two numbers for a budget bar.

**And every hand-built aggregate carries `horizon_condition()`.** This is the `/time/report`
lesson and it is a data leak, not a slow page: the rows rode `scoped_select()` (horizon
included) while the totals were a bare `select(func.count())` over the org, so a
company-group-scoped manager read a list of one under a header saying two. The moment a read
leaves the repository's path — a window fold, a report, a summary tile, a panel total — take the
predicate from `horizon_condition()` explicitly (CLAUDE.md §15, and `scoped_count_select()` for
plain counts).

## Read the windows, not their hull

A screen that compares two spans reads *two ranges*, never `[earliest, latest]`. While the
comparison was always the span immediately before, the two were the same statement and the hull
was free. Once the comparison could be **the same span a year earlier** (#312), the hull became a
year of daily rows fetched to print thirty — three years' worth on the 12-month range — and
nothing in the response would have shown it, because the Python that buckets the rows filters
correctly either way. `MarketingService._metrics_for_links` takes a list of spans and ORs two
bounded predicates, which keeps the index scan on `(org_id, link_id, date)` and the row count at
what the screen draws.

Its test asserts the **statement**, not the numbers: two lower bounds means two windows, one
means the hull, and the KPIs are identical in both cases. The same shape as every other rule
here — the regression is invisible in the JSON.

Its sibling: resolving a setting that has an org-level default and a per-row override is **one**
statement, not two. Both are single-row unique-index lookups, so they ride as scalar subqueries
on one FROM-less `SELECT` (which Postgres answers with exactly one row whether or not either row
exists — the distinction the read needs anyway, since absent means *the default* on both sides).
Two would have been invisible everywhere except the company hub, which composes a provider per
enabled module in sequence and is exactly where "+1 each" adds up.

## Bound every read; a truncated count is a lie

A panel or a detail response that grows with the tenant's history will be fine for a year and
then be the reason someone's client page takes six seconds. Cap them: the time panel's recent
list, task comments (200, newest-first then reversed — taking the *first* 200 shows the oldest
and hides the conversation), the activity trail (50), the contact picker's opening options (20,
with the picker searching the API as you type).

**A capped list's count is counted, not measured.** `len(items)` behind a `LIMIT 50` reported
"50 open" for a client with 300 — a wrong number, not a rounded one. Use
`scoped_count_select()`.

## A list screen pages; a panel caps and says so

The two rules above are about *panels* — a section of somebody else's page, where the honest
answer is a bounded slice plus a sentence admitting it. **A list screen is the opposite**: it is
the place the whole set lives, so it may not be a sample of itself. Every one of them now pages
(`$lib/core/table/paging.ts` + `$lib/core/ui/Pagination.svelte`).

The rule this replaced was "ask for 200 and hope", and it failed the way truncation always fails:
a tenant whose client list outgrew the cap got a *prefix* that looked exactly like the whole
answer, and the only route to row 201 was guessing a narrow enough search term. Four properties
hold the replacement up, and a new list gets all four or it is not done:

1. **The URL is the view.** `?page=` (1-based, absent on page 1) and `?size=` fully describe the
   slice on screen. That is what makes the back button land where the user left, a page
   shareable, and SvelteKit's scroll restoration correct — and it is why the controls are `<a
   href>`, never click handlers. "I opened a client from page 4 and came back to page 1" is the
   bug this shape prevents.
2. **The load resolves it, the API applies it.** `resolvePaging(event.url, pref)` in
   `+page.server.ts` returns `{page, limit, offset}`; hand `limit`/`offset` straight to the
   endpoint and return `paging` alongside `total`. Never slice in the browser.
3. **Every filter, search and sort control drops `page`** (`resetPage`). Page 7 of the old filter
   is usually nothing at all in the new one, and an empty page reads as "the filter found
   nothing". `SearchInput` and `createTableLayout.onSort` already do it, and a filter declared
   through `$lib/core/filters` inherits it — which is most of why bespoke filter code stopped
   being written: every hand-rolled copy was one `resetPage` away from this bug.
4. **The size is a personal default, not state.** `TablePref.page_size` (50 by default; 25 / 50 /
   100 / 200 offered) is saved per user per list beside the column layout, and the URL overrides
   it whenever it speaks. Storing the current *page* in the preference instead would make two
   tabs fight over one number and break the back button outright. A screen with no table
   preference to hang it on passes no `onsize` — the choice then lasts the visit, which is fine.

Two consequences worth stating. **A filter that was applied in the browser is now a bug, not a
shortcut**: the companies status pill filtered `data.companies` client-side, which was survivable
only while the page *was* the list — against a paged list it narrows the fifty rows you happen to
hold and reports a total counted over all of them. It moved to the API, where the export already
sent it. And **a group count inside a paged list counts the page**, so a sectioned list says so
(`contacts.groups_page_only`), exactly as a capped panel does.

**A panel cap is the list's own first page, taken through the list's own read.** The domains
company panel used to call a bespoke `domains_for_company` with no limit at all: to draw five
names on a client card it loaded the client's entire portfolio, resolved every party label and
every TLD price on it, and threw the rest away. Both asset panels now call the module's `list`
with `limit=5` — the same conditions, the same default sort, the same batched `_attach` — and
send `total` beside the rows so the card can link on with an honest number. Taking the slice
*through the list's read* is what makes the "Alle 23 bekijken" link continue the card rather
than open a differently-ordered set, and it is one fewer query shape to keep correct.

The other half of that change is worth stating on its own: **a filter that crosses a module
boundary belongs in the statement, not in Python.** A website's client is its parent domain's
(CLAUDE.md §6 forbids the import, so it is a bare-table bridge), and the first implementation
selected every one of that client's domain ids and passed them back as an `IN` — an unbounded
read whose cost tracked the client's register rather than the page, paid twice because the count
statement re-ran it. As a correlated `EXISTS` it is one more predicate on a statement that was
running anyway, and the same bridge then carries the list's `q` for free. Pinned by
`test_website_company_filter_never_reads_the_clients_domains`, which measures the statement count
at two rows and again at six: the endpoint returns identical JSON either way, so nothing else
would have caught it.

The exceptions are real but narrow, and each is an exception for a reason offset paging cannot
serve: a **grouped inventory** (Cloudflare zones, listed under their account) would split a group
across pages; a **grouped report with subtotals** (`/invoices/uninvoiced`) computes its totals
over the whole set by design; an **approval queue** (`/leave/team`'s pending list) is meant to be
emptied, and a second page of decisions waiting on you is a workload problem, not a paging one.

## Per-request overhead is pinned

`require_context` resolves the membership, its effective permissions and "does it hold the client
role" on **one** statement. Two things followed from that and are now rules:

- **A scope resolver must never re-query a fact the statement already answered.** The client-role
  horizon floor re-ran its own `EXISTS`, and `is_portal` re-ran the contacts join the portal scope
  resolver had just finished — so every non-owner request in the app, staff included, paid a
  contacts read to be told the caller was not a client. Resolvers are keyed
  (`app/core/scope.py`); a caller who knows an answer hands it in.
- **The budget is a test, not a memory.** `tests/test_perf_query_budgets.py` pins an ordinary
  member's request at eight statements. Moving that number is allowed; moving it by accident is
  not.

## Permissions resolve once per request, and are not cached

`require_context` resolves the caller's effective permissions on the **same statement** as the
membership lookup — an `outerjoin` through `membership_roles` into `role_permissions` with
`array_agg(...).filter(...)` (issue #19). Same query count as before RBAC existed, whatever the
number of roles a person holds. `tests/test_rbac_model.py` asserts it with `count_queries`.

Two rules follow, and both are load-bearing:

- **Never re-query a permission.** `ctx.can(...)` / `ctx.require(...)` read an in-memory
  `frozenset` on `RequestContext`. A service that hits the database to answer "may they?" is a
  bug, however small it looks.
- **There is no Redis cache for permissions, on purpose.** One indexed join beats cross-process
  invalidation, and a stale permission cache is a security bug rather than a slow page. Revisit
  only with a measurement.

Anything that renders *who may do what* is a grouped query too, never one per row:
`GET /roles` gets its permissions and member counts in two grouped statements; `GET /members`
carries each membership's `role_ids` in one, so the Gebruikers screen derives every user's
effective set client-side instead of asking the API per member; `permission_holder_ids` builds
every people-picker as a single `DISTINCT` query (a user holding two granting roles would
otherwise appear twice).

The roles and the permission catalog are shared by two Instellingen screens, so they load in
`settings/+layout.server.ts` — a layout load does not rerun on tab navigation — and only for a
user who can actually manage roles.

## Never hold a DB connection across an external call

A tenant request runs as **one transaction on one pooled connection**, held from its first
query until the response commits — that is what keeps the RLS GUC bound (`app/db.py`). The
corollary: every second a handler spends awaiting something *other* than the database is a
second it starves the pool for everyone else.

This melted production down (2026-07-14). The marketing tab's range switch fired six
cache-miss drill-downs, each awaiting GSC/GA4 for seconds **while pinning a pool
connection**. With the then-default pool (5 + 10 overflow per process, one uvicorn process)
a couple of page navigations on top exhausted all 15; every other request — `/prefs`,
`/meta/me`, everything — queued 30 s on checkout and then 500'd. To a user that is the whole
app freezing, every couple of minutes, for half a minute.

Two rules follow:

- **Wrap every in-request external HTTP call in `ctx.release_db()`** (`RequestContext`,
  `app/core/tenancy.py`). It commits the transaction — handing the connection back to the
  pool — runs your block, and re-binds the RLS GUC on a fresh transaction afterwards. Inside
  the block the session is off-limits (a query would run without the GUC and fail closed);
  do your reads before, your writes after. With `acting_as(...)`, enter it *first* (it reads
  settings), then `release_db()`. Worker jobs don't need this — they run in their own
  process and pool.
- **Better still: don't call external services in the request path at all.** A short Redis
  TTL in front (drive browse, marketing drill-downs) or the ARQ worker + stored rows
  (marketing metrics, calendar events) keeps request latency ours, not Google's.

The pool itself is sized in `config.py` (`SCHAKL_DB_POOL_SIZE` / `_MAX_OVERFLOW` /
`_TIMEOUT_SECONDS`, defaults 15/15/5 s) — sized for the SSR fan-out, timing out fast because
a request that waits the full `pool_timeout` freezes a browser tab for that long before
failing anyway. Never fall back to the SQLAlchemy defaults (5/10/30 s).

## Case study: the My Day dashboard

The dashboard composes widgets contributed by modules (`(app)/+page.server.ts`). Fixes applied
(and the reasoning, so the pattern is reusable):

1. **Hidden widgets do no work.** The small `GET /api/v1/dashboard/prefs` request resolves the
   saved layout first, then only selected widget loaders run. This deliberately adds one small
   dependency but avoids every hidden widget's HTTP and aggregate-query fan-out.
2. **The "open tasks by group" widget** fetched tasks + projects + companies (200 each) with
   full aggregates just to map ids→names. It now calls `/tasks/dashboard-groups`, which returns
   the finished groups from one compact aggregate query. The personal tasks widget likewise
   skips label/checklist/comment enrichment it never renders.
3. **The team-month widget uses one bounded aggregate.** It previously requested a paginated
   report and a two-year revenue series to display four numbers. `/time/stats/team-summary`
   returns those four values from one period-bounded database query.
4. **Identical widget GETs coalesce per render.** Registry composition stays independent, while
   widgets requesting the same digest share one promise. In particular, the two invoicing tiles
   now cause one API/context/DB request rather than two.
5. **Tiles stream independently.** The page returns selected widget promises after resolving the
   small layout preference. The shell and skeletons render immediately, and each usable tile
   replaces its skeleton when ready; the slowest integration no longer gates the whole board.

The Hours screen follows the same rule: `/time/workspace` combines timer, weekly grid, selected
day, draft, and recent-entry defaults. Besides removing three authenticated HTTP round trips, it
reuses the weekly entry scan for the selected day instead of reading those rows twice.

If you bound coverage for speed (top-N, sampling, no-retry), say so in the UI/logs — silent
truncation reads as "we showed everything" when we didn't.

## Every perf property gets a `count_queries` test

`tests/conftest.py`'s `count_queries` fixture is the regression harness, and
`tests/test_perf_query_budgets.py` is where the budgets live. An endpoint that is one grouped
query at three rows and one-per-row at three hundred returns *identical JSON* either way — no
functional test can tell them apart, so the shape has to be asserted directly.

What lands with a test: every new list endpoint or panel (a query budget), every N+1 fix (`len(
counter.matching("from <table>")) == 1`), every opt-out (on *and* off), and any fix whose
correctness depends on a count (the horizon on an aggregate).

**The company hub has an umbrella budget.** `GET /companies/{id}/panels` runs thirteen providers
in sequence on one `AsyncSession` — correct, and the reason the hub's cost is the *sum* of its
panels. No individual panel review catches "each one added a query"; the umbrella test does.
Raise the number knowingly when a panel is added, with that panel's own budget beside it.

## Compression is the edge's job

Traefik's `compress` middleware, in all four `infra/traefik/dynamic*.yml` and in the
custom-domain fragment the API generates (`compress@file` — a bare middleware name only resolves
within one file). **Not** FastAPI's `GZipMiddleware`: it would buffer the AI assistant's SSE
stream, turning token-by-token delivery into one late blob, and sit in front of `/mcp`, whose SDK
owns its own transport framing. `text/event-stream` is excluded by name anyway. Under 1 KB stays
uncompressed — below that the header and CPU cost more than the saving.

## Indexes follow the hot filter

Composite, `(org_id, …)`-leading, declared in the model's `__table_args__` **and** the migration
so `alembic check` stays quiet. **Check the leftmost prefix before adding one**: a composite
starting `(org_id, project_id, …)` does nothing for a query that narrows by `company_id`, and a
plain btree on `name` does nothing for `ORDER BY lower(name)` — an index the planner will not
choose is write cost with no read benefit. That last check removed two of the eight indexes
#290 originally proposed.

## The web build's heap is declared, because the module graph outgrew Node's default

`apps/web` builds ~8,700 modules, and the count is dominated by two things that are each
correct on their own: Paraglide compiles **one module per message** (4,159 keys × the locales,
§8) and `@lucide/svelte` ships **1,746 icon components** behind the barrel that 112 files
import from. Rollup holds that whole graph in memory, so the client build's live set passed
Node 22's default old-space cap (~4.3 GB on a 16 GB machine) and every `vite build` died with
`Ineffective mark-compacts near heap limit` — exit **134**, not a lint or a type error.

So `apps/web`'s `build` script states its own ceiling
(`NODE_OPTIONS=--max-old-space-size=6144`). It lives in `package.json` rather than in the CI
workflow because **three** places run this build and only one of them is CI: the `web` job, the
release image's builder stage (`apps/web/Dockerfile`), and a developer's own machine. A flag set
in the workflow fixes the red check and still ships a release that cannot build.

Two things follow. The SSR pass is *not* where it fails — that one completes at ~4 GB and the
client pass dies right after it, so a log that ends just past `✓ 8702 modules transformed` is
this and not a code error. And the ceiling is a symptom worth watching: when it needs raising
again, prefer spending the graph down first — deep icon imports (`@lucide/svelte/icons/<name>`)
retire ~1,700 modules for a mechanical change, which is the cheaper fix and the one that also
makes every dev server start faster.

## Checklist for any new screen or endpoint

- [ ] Counted the calls/queries; each one is necessary.
- [ ] Independent calls run in `Promise.all`, not in series; the entity is *in* the fan.
- [ ] URL-independent lookups are in the **section layout**, not the page load.
- [ ] Modal-only and report payloads stream, resolved into `$state` (never a raw `{#await}`).
- [ ] `count=false` / `meta=false` / `with_body` / `lines=false` on anything whose extra work
      you discard — and a new opt-out if this list ships what its screen never draws.
- [ ] No 200-row fetch to show a handful; no heavy aggregate to render a label.
- [ ] Aggregates are computed in SQL, and every hand-built one carries `horizon_condition()`.
- [ ] Every unbounded read is capped, and every capped list's total is *counted*.
- [ ] A **list screen** pages: `resolvePaging` in the load, `<Pagination>` under the table,
      `resetPage` on every filter/search/sort control, and no filtering done in the browser.
- [ ] No external HTTP call while holding the request's DB connection — `ctx.release_db()`
      around it, or move it behind the worker/cache entirely.
- [ ] A `count_queries` budget test lands with it.
- [ ] Links that lead somewhere preload on hover.
- [ ] After moving keys between loads: cross-checked every `data.x` by hand (`svelte-check`
      does not).
