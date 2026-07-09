# Workflow — issues, labels, branches, commits

> How work is picked up and landed here. Read this before your first commit in a session.
> The rules exist because **several agents work this repo at the same time**: the working
> tree you see is not necessarily yours.

## The one rule

**Only touch, stage, and commit the files you changed.** Never `git add -A`, never
`git add .`, never `git commit -a`. Stage explicit paths:

```bash
git add apps/web/src/lib/core/ui/TimeInput.svelte docs/UX.md   # yes
git add -A                                                     # no
```

`git status` will routinely show modified and untracked files belonging to another agent
mid-task. They are not yours to commit, revert, stash, or clean up. If you have already
swept one in, undo it before pushing:

```bash
git reset --soft HEAD~1
git restore --staged <files that are not yours>
git commit -F -   # re-commit only your paths
```

Note that this rewrite gives your commit a **new SHA**. Re-read it with `git log -1
--format=%h` before quoting it anywhere — do not reuse the hash you printed earlier.

## Branches

- `main` — default and release branch. PRs target it. Do not push to it directly.
- `dev` — integration branch. **Agents may commit and push here directly**; no PR needed.
- Version tags cut the release and build images (`.github/workflows/release.yml`).

### Pushing to `dev` without publishing someone else's work

Another agent may have committed on top of you locally. A plain `git push` would publish
their commit too. Push *your* commit by SHA instead, and check first:

```bash
git fetch origin
git log --oneline origin/dev..dev          # what is unpushed, and whose is it?
git push origin <your-sha>:dev             # fast-forwards dev to yours only
```

Their commit stays local, theirs to push. If the push is rejected as non-fast-forward,
someone landed first: `git fetch`, rebase your commit onto `origin/dev`, re-run the checks,
and try again. **Never force-push a shared branch.**

## Commits

Conventional, small, scoped: `feat(time): add weekly timesheet grid`,
`fix(web): stamp brand variables on <html>`. The scope is the module or area.

The body explains *why*, not what the diff already shows. Close issues with `Closes #N` —
it fires when the commit reaches `main`, not `dev`, so an issue worked on `dev` stays open
until release. Say so in the issue rather than assuming it closed.

End the message with:

```
Co-Authored-By: Claude <noreply@anthropic.com>
```

## Issues

Comment on the issue you worked, and be specific:

- The **real** commit SHA (see the rewrite warning above) and the exact files it contains.
- The acceptance criteria, ticked or not, each with the evidence.
- **What you did not verify.** There is no repo-committed web test harness — no vitest, no
  Playwright test suite. If you have a Playwright MCP server configured (personal, local
  setup — not every agent has one), use it to actually click through the change before
  claiming it works. If you don't, `svelte-check`, lint, a compile, and pure-logic assertions
  are the ceiling for a UI change. Say plainly which one happened — a real click-through, or
  static checks only — instead of implying a browser touched it when it didn't.
- Anything the next agent needs: a conflict you expect, a file another agent had open.

## Classifying an issue

Four things classify an issue, and only one of them is labels. **Type, priority, milestone
and relationships are native GitHub fields** — reach for a label only for what none of them
express.

| Axis | Where it lives | Values |
|---|---|---|
| Type | native issue **type** (org-level) | `Bug` · `Feature` · `Task` |
| Priority | native issue **field** (org-level, single-select) | `Urgent` · `High` · `Medium` · `Low` |
| Phase | native **milestone** | `P1 MVP` · `P2 Agency core` · `P3 Google Workspace` · `P4 Automation & public API` |
| Structure | native **sub-issues** and **dependencies** | parent ↔ child · blocked-by ↔ blocking |
| Everything else | **labels** | see below |

A field beats a label because it is single-valued, sortable, and rendered in the issue
sidebar rather than as one chip among ten. The `bug`, `enhancement`, `tech-debt`,
`priority: *` and `phase: *` labels **no longer exist** — they were deleted once their
values moved into the fields above. Do not recreate them.

### Type

Set by the issue templates on creation (`type:` in the form YAML), so most issues arrive
typed. `Task` covers chores, docs, and tech debt — anything that is neither a defect nor new
functionality.

`epic` is still a **label**, not a type: the org's type list is `Bug`/`Feature`/`Task`, and
adding to it needs `admin:org` scope. An epic is a `Feature` carrying the `epic` label and
owning its children as sub-issues. If someone later creates an `Epic` type, migrate the five
epics and delete the label.

### Priority

Not on the issue by default — **set it at triage**, every time. It is an organization issue
field, so `gh issue edit` cannot touch it; use the REST API with the field's numeric id:

```bash
gh api /orgs/vlotr-crm/issue-fields -q '.[] | "\(.id)\t\(.name)"'   # 43901503 = Priority

gh api -X PATCH /repos/vlotr-crm/vlotr/issues/<N> \
  -f 'type=Bug' \
  -F 'issue_field_values[][field_id]=43901503' \
  -f 'issue_field_values[][value]=High'
```

Read it back from `.issue_field_values[].single_select_option.name` — it is **not** in
`gh issue view --json`.

### Milestone = phase

The phases are the build gates in `CLAUDE.md` §10. An issue with no phase yet simply has no
milestone; do not guess one to fill the bar.

```bash
gh issue edit <N> --milestone "P2 Agency core"
```

### Relationships

Express structure with the native links, never by writing "part of #16" in the body and
hoping. An epic's children are **sub-issues**; work that cannot start until another lands is
**blocked by** it.

```bash
gh issue edit 21 --parent 22                    # sub-issue of the google epic

id=$(gh api /repos/vlotr-crm/vlotr/issues/19 -q .id)          # note: .id, not the number
gh api -X POST /repos/vlotr-crm/vlotr/issues/20/dependencies/blocked_by -F issue_id=$id
```

### Labels

What remains is genuinely multi-valued — an issue can be several of these at once, which is
exactly why they are not fields.

| Group | Labels |
|---|---|
| Facet | `security` · `i18n` · `ux` · `documentation` · `epic` · `question` |
| Status | `needs testing` |
| Triage | `duplicate` · `invalid` · `wontfix` · `good first issue` · `help wanted` |
| Area | `area: ` + `api` · `auth` · `automation` · `billing` · `calendar` · `companies` · `dashboard` · `google` · `infra` · `integrations` · `mcp` · `notifications` · `projects` · `rbac` · `tenancy` · `time` · `web` |

The area prefix is `area:` **followed by a space** — `area: companies`, not `area:companies`.
`gh` matches label names exactly and fails the whole command on a miss, so the wrong form
takes the rest of your `--label` flags down with it.

A facet crosses types: a `Bug` can be `security`, a `Feature` can be `i18n`. That is the
test for whether something belongs here rather than in the type.

`needs testing` means: implemented and pushed, but the behaviour was never exercised in a
browser. **Apply it to any UI change you land that you did not personally click through** —
if you have a Playwright MCP server configured, drive the real flow yourself and skip the
label; without one, that covers nearly all UI changes. When you do apply it, list in the same
comment the clicks that would clear it. Remove it once someone confirms.

Prefer an existing label. Create one only when no existing label fits, keep it lowercase,
give it a description, and reuse the group's colour (`#0052cc` for areas).

```bash
gh issue edit <N> --add-label "needs testing"
gh issue comment <N> --body "…"
```

## Breaking database changes need a migration plan

**Every schema change ships to databases that already have data in them.** Agencies
self-host (CLAUDE.md §5) and upgrade by pulling a new image tag. The API entrypoint runs
`alembic upgrade head` under `set -e` before uvicorn binds (`docs/DEPLOY.md`), so the
migration runs **unattended, on someone else's production data, with no operator watching**.
Nobody reviews it at that moment. You are the review.

So a change is not "add a column" — it is "move every existing release from its schema to
this one without losing a row". Before writing the migration, answer in the PR or the issue:

- **Which released versions upgrade into this?** Self-hosters skip tags. A migration must
  apply on top of any older `head`, not just the one on your laptop.
- **What happens to existing rows?** A new `NOT NULL` column needs a server default or a
  backfill in the same migration. Never a `NOT NULL` with no default on a populated table —
  it aborts the upgrade and the API never starts.
- **Is it reversible?** Write a real `downgrade()` — all current migrations have one. A
  half-applied upgrade on a stranger's server is the failure you are guarding against.
- **Can the previous image still run against the new schema?** The entrypoint migrates on
  start, so rolling the image tag *back* leaves old code on a new schema. A migration that
  drops or renames anything the previous release still reads makes rollback impossible.

### Destructive changes go out over two releases (expand / contract)

Never drop, rename, or retype a populated column in the release that stops using it.

1. **Expand** — add the new column/table nullable, backfill it, write to both, keep reading
   the old one. Ships in release *N*. Safe to roll back: the old code ignores the new column.
2. **Contract** — once *N* is out and adopted, stop reading the old column, then drop it in
   release *N+1*.

A rename is expand/contract, not `op.alter_column(new_column_name=…)`. A type change is
expand/contract. Splitting or merging a table is expand/contract.

### Rules

- Backfills must be **idempotent** and must not assume they run once, or on a small table.
- Data migrations that touch tenant rows are **per-org and `org_id`-scoped** like everything
  else (Golden Rule 1); RLS is on, and the migration runs as `vlotr_app`, not a superuser.
- One migration per change, named `<module>_<verb>_<noun>` (CLAUDE.md §9).
- Test the upgrade against a database with rows in it, not an empty one. An empty database
  will happily accept a migration that destroys a populated one.
- If a change genuinely cannot be made non-destructively, say so on the issue **before**
  building it, and write the operator steps (backup, downtime, order) into `docs/DEPLOY.md`.

## Definition of done

`CLAUDE.md` §9 governs: migration, endpoints + tenant scoping, web UI, **both** `en.json`
and `nl.json`, a tenant-isolation test, OpenAPI. On top of that, before you call an issue
done: `pnpm run check` clean, lint no worse than baseline, `i18n:check` passing, the issue
commented, and the label applied. If the change touches the schema, the upgrade plan for
existing releases is written down and the migration was run against a populated database.

Lint is currently **red on pre-existing errors** (`svelte/prefer-writable-derived`,
`svelte/prefer-svelte-reactivity`). Do not add to the count, and do not fix unrelated ones
in your diff — that is someone else's file.
