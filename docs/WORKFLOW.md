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
- **What you did not verify.** There is no web test harness in this repo — no vitest, no
  Playwright, no browser. `svelte-check`, lint, a compile, and pure-logic assertions are the
  ceiling for a UI change. Say that plainly instead of implying a click-through happened.
- Anything the next agent needs: a conflict you expect, a file another agent had open.

## Labels

Every issue carries a **type**, a **priority**, and one or more **areas**. Phase labels come
from the plan in `CLAUDE.md` §10.

| Group | Labels |
|---|---|
| Type | `bug` · `enhancement` · `documentation` · `tech-debt` · `security` · `i18n` · `ux` · `epic` · `question` |
| Priority | `priority: critical` · `priority: high` · `priority: medium` · `priority: low` |
| Phase | `phase: P1` · `phase: P2` · `phase: P3` · `phase: P4` |
| Status | `needs testing` |
| Triage | `duplicate` · `invalid` · `wontfix` · `good first issue` · `help wanted` |
| Area | `area:` + `api` · `auth` · `automation` · `billing` · `calendar` · `companies` · `dashboard` · `google` · `infra` · `integrations` · `mcp` · `notifications` · `projects` · `rbac` · `tenancy` · `time` · `web` |

`needs testing` means: implemented and pushed, but the behaviour was never exercised in a
browser. **Apply it to any UI change you land** — given the missing harness, that is nearly
all of them — and in the same comment list the clicks that would clear it. Remove it once
someone confirms.

Prefer an existing label. Create one only when no existing label fits, keep it lowercase,
give it a description, and reuse the group's colour (`#0052cc` for areas).

```bash
gh issue edit <N> --add-label "needs testing"
gh issue comment <N> --body "…"
```

## Definition of done

`CLAUDE.md` §9 governs: migration, endpoints + tenant scoping, web UI, **both** `en.json`
and `nl.json`, a tenant-isolation test, OpenAPI. On top of that, before you call an issue
done: `pnpm run check` clean, lint no worse than baseline, `i18n:check` passing, the issue
commented, and the label applied.

Lint is currently **red on pre-existing errors** (`svelte/prefer-writable-derived`,
`svelte/prefer-svelte-reactivity`). Do not add to the count, and do not fix unrelated ones
in your diff — that is someone else's file.
