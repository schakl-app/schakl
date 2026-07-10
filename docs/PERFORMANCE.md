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
- **Shared lookups live in a layout load**, not the page load. Layout loads do **not** rerun
  on filter/tab/day navigation, so companies/projects/labels/members are fetched once per
  section (`(app)/tasks/+layout.server.ts`, `(app)/time/+layout.server.ts`,
  `(app)/overview/+layout.server.ts`), not on every click. Page loads fetch only what changes.
- **Never `await` calls in series when they're independent.** Use `Promise.all`. A sequential
  chain of N calls costs N round-trips; the same calls in parallel cost one.
- **Links preload on hover** (`data-sveltekit-preload-data="hover"`) so the next page's load
  starts before the click.

## Lighten the calls you keep

Two opt-outs exist on the list endpoints for exactly this — **use them wherever the extra
work is discarded**:

- **`count=false`** — skip the `SELECT count(*)` that computes `Page.total`. Pass it from
  name-only lookups and pickers and any widget that never shows a total (companies, projects,
  tasks list endpoints support it). `total` then returns the page length.
- **`meta=false`** (tasks) — skip the per-task label/checklist/comment aggregate subqueries.
  Pass it whenever you only need id/title/status/dates (grouping, pickers, the timesheet
  lookups).

Don't fetch heavy aggregates to render a label. Don't request 200 rows to show 5.

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

## Case study: the My Day dashboard

The dashboard composes widgets contributed by modules (`(app)/+page.server.ts`). Fixes applied
(and the reasoning, so the pattern is reusable):

1. **Prefs no longer gate the widgets.** `GET /api/v1/dashboard/prefs` only orders/filters the
   already-known available widgets, so it runs *inside* the same `Promise.all` as the widget
   loaders instead of being `await`ed first — one fewer sequential round-trip.
2. **The "open tasks by group" widget** fetched tasks + projects + companies (200 each) with
   full aggregates just to map ids→names. It now passes `meta=false` (skips the task
   aggregates it discards) and `count=false` on all three (skips the discarded COUNTs).

### Still on the list (documented, not yet done)

- `tasks.my_open` and `tasks.by_group` both fetch tasks — "mine" is a subset of the full list
  the group widget already pulls. A shared fetch could serve both.
- `time.team_month` pulls a whole-month report **and** a full-year revenue series to render a
  few tiles; both could be scoped to just the tile inputs.

If you bound coverage for speed (top-N, sampling, no-retry), say so in the UI/logs — silent
truncation reads as "we showed everything" when we didn't.

## Checklist for any new screen or endpoint

- [ ] Counted the calls/queries; each one is necessary.
- [ ] Independent calls run in `Promise.all`, not in series.
- [ ] Shared lookups are in the layout load, not refetched per navigation.
- [ ] `count=false` / `meta=false` on anything whose extra work you discard.
- [ ] No 200-row fetch to show a handful; no heavy aggregate to render a label.
- [ ] Links that lead somewhere preload on hover.
