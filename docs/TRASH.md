# The trash can — a delete you can take back, and a record you cannot lose

> How deleting a record works since v0.46: soft delete with a retention window, a purge that
> refuses to destroy anything that has to outlive the record, and one screen where the way back
> is. Read this before adding a `DELETE` route or a `ON DELETE CASCADE` on a domain table.

## The two rules

**Deleting is trashing.** `DELETE /api/v1/companies/{id}` stamps `deleted_at` and nothing else
changes. The row and every row that *belongs* to it (its tasks, its contact moments, its drafts)
disappear from every list, picker, search, panel, dashboard and MCP tool, and a restore brings
them all back exactly as they were, because nothing was moved. After a retention window (30 days,
`TRASH_RETENTION_DAYS`) the nightly sweep runs the delete the database always did.

**A record with a history cannot be deleted at all.** An issued invoice or quote, a domain, a
hosting account, an agreement, a project and logged hours each have to outlive the client they
belong to — in the books, at the registrar, on the server, in the timesheet. A client holding any
of them answers `409 errors.trash_blocked` with the counts in `details.blocking`, the dialog says
which and how many, and offers **Archiveren** instead. This is the fix for what deleting a client
used to do: the `invoices` and `quotes` cascade took paid, numbered, ledger-booked documents that
`DELETE /invoices/{id}?force=true` would have refused, and stranded `time_entries.invoiced_at` on
hours that could then never be billed again. Archive is the lifecycle for a client you are done
with; the trash is for a mistake.

## How it is built

Core owns the mechanics (`app/core/trash/`); a module describes its shape — the panels pattern
(CLAUDE.md §6), applied to deletion.

- **`TrashableMixin`** (`mixin.py`) adds `deleted_at`, `deleted_by_user_id` and `deleted_by_name`
  (an id while the account exists and a snapshot for after it does not — the trail's rule, §16).
  `companies` is the first and so far only entity that carries it.
- **`TrashableSpec`** on the owning module's `ModuleDescriptor.trash`: the model, the delete
  permission the three verbs ride, and how a row is named on the trash screen.
- **`TrashDependent`**, registered by every *other* module that holds rows about the entity
  (`register_trash_dependent("company", …)` in its package `__init__`, from its own `trash.py`):
  a namespaced key, an i18n label, a batched counter, `blocks: bool`, and optionally a `purge`
  hook for what a cascade cannot do well. Core composes the counts into the dialog, the refusal
  and the purge, and names no module.
- **The predicate lives in the repository** (`TenantScopedRepository.trash_condition`). A
  trashable model hides its trashed rows; a model with a `company_id` column hides the rows whose
  client is in the trash, through a `NOT EXISTS` against an alias of `companies` (so a statement
  that already joins `companies` for a label keeps a FROM). `horizon_condition()` now returns
  *visibility* — the company horizon **and** the trash — so every hand-built read that ANDs it on
  (the interactions feed, the time summaries, the marketing picker) got the trash for free. A
  portal repository overrides `company_horizon`, never `horizon_condition`, or it drops one half.
  `ctx.repo(model, include_trashed=True)` is the one door past it, used by the trash service and
  by a contributing module's purge hook (a trashed client's tasks are hidden from the tasks repo
  too). `ensure_parent_in_tenant` refuses a trashed parent, and the modules with a raw
  `SELECT 1 FROM companies` check learned `AND deleted_at IS NULL`.
- **Routes are generated per entity** (`router.py`, the bulk router's shape): `GET /trash/company`,
  `GET /trash/company/{id}`, `GET /trash/company/{id}/preview`, `POST …/restore`, `DELETE …`,
  each declaring `companies.company.delete` — deny-by-default stays enumerable, and every verb is
  a named MCP tool (`trash_restore_company`). No new permission: the person who may delete a row
  is the person who may undo it.
- **The sweep** (`jobs.py`, `trash_purge`, 03:00, before the storage sweep) purges per row in its
  own SAVEPOINT as the system; a row that has grown a blocker since it was trashed is kept and the
  screen says why.
- **The trail** records `trashed`, `restored` and `purged`, the last one *before* the row goes,
  carrying the label — the invoice-number rule.

## What a client takes with it

| Dependent | Module | Blocks? | At purge |
|---|---|---|---|
| Issued invoices, credit notes, quotes (`number IS NOT NULL`) | invoicing | **yes** | — |
| Domains | domains | **yes** | — |
| Hosting accounts | hosting | **yes** (a clientless account is a real state, but not one to arrive at by accident) | — |
| Agreements | subscriptions | **yes** | — |
| Projects | projects | **yes** (the service refuses ever to clear a project's client) | — |
| Time entries on the client | time | **yes** | — |
| Draft invoices and quotes | invoicing | no | cascade |
| Tasks | tasks | no | the tasks module deletes them through its schedule service, so a mirrored calendar block is taken back — a task must have a client now, so the FK's `SET NULL` would leave rows nothing can edit |
| Contact-person links | contacts | no | the link cascades; the person stays |
| Contact moments | interactions | no | detached (`SET NULL`), kept — a contact moment is a record of something that happened |
| Marketing links | marketing | no | cascade (the synced history goes; the dialog says so) |
| Documents pinned to the client | core (`files.py`) | no | `drop_file` per row, so shared bytes stay shared |

Not named in the dialog and cascading silently, because they are mirrors of state that lives
elsewhere: company assignees and group memberships, SnelStart and Timeon pairings, marketing
company settings, report profiles. `reports` carry no FK on purpose and outlive the client (their
own rule, `docs/REPORTING.md`).

## The screens (`docs/UX.md`)

- **The delete dialog** (`CompanyDeleteDialog`) reads `GET /trash/company/{id}/preview` when it
  opens and is a choice between two ways out, the invoice cancel dialog's shape: a radio posting
  as `mode`, **Archiveren** first and `primary` (it destroys nothing), **Naar de prullenbak** red
  and disabled with the reason in numbers for a client with a history. The confirm button is
  held until the preview has answered. The bulk delete trashes what it can and reports the rest
  per row (`errors.trash_blocked`).
- **The undo** is a toast with *Ongedaan maken*: the list page shows it after its own delete and
  after a detail-page delete lands with `?trashed=<id>`, then strips the marker.
- **Instellingen → Prullenbak** (`/settings/trash`, Data & lijsten, gated on any trashable
  entity's delete permission) lists what is in the trash newest-first: type, label, who and when,
  *definitief weg op* or *blijft bewaard: heeft …*, and what goes with it. **Terugzetten** is
  inline; **Definitief verwijderen** is behind the ⋯ and asks for a tick. A client list shows
  *Prullenbak (n)* in its header only while n > 0, and opening a trashed client's own URL
  redirects whoever may restore it to the trash with the row marked.

## Adding a second trashable entity

1. Add `TrashableMixin` to the model and an additive migration (three nullable columns, a partial
   index on `(org_id, deleted_at) WHERE deleted_at IS NOT NULL`).
2. Declare a `TrashableSpec` on the module's descriptor; route the service's `delete` through
   `TrashService.trash`.
3. Have each module holding rows about it register a `TrashDependent`, deciding `blocks` by one
   question: must this outlive the record, in the world or in the books?
4. If the entity is not `companies`, teach `TenantScopedRepository.trash_condition` how its
   dependents anchor to it — today the anchor is the `company_id` column and nothing else.
5. Add the entity to `TRASH_ENTITIES` on the web, and its `trash.entity.<type>` label.
