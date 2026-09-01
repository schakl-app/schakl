# Client portal — logins for the people you work for

> The `portal` module (issues #193, #296). Read this before adding a second kind of client
> login, before moving the invite control, and before assuming the portal is part of `contacts`.

A client gets a login into the agency's own app and sees exactly their own companies: their
dashboards, their invoices, their tasks' comments. Nothing else. Three separate mechanisms make
that true, and they live in three different places on purpose.

## Why it is a module

Everything the portal does happens *on a contact's page*, which is why it started life inside
`contacts`. It is still the wrong home:

- **It is a product the agency buys**, not a property of the address book. It carries a `sku`,
  so an instance without a licence for it can read what exists and invite nobody new (§ below).
- **It has its own lifecycle.** Turning it off must stop new invites without touching a single
  contact row.
- **Its subject is not necessarily a contact.** A supplier login, a company-level login, a login
  for a person who is not in the address book at all — each is a provider registration, not an
  edit to `contacts`.

So: `app/modules/portal/` owns the invite, the disable, the impersonation and the screen. What
stays in `contacts` is the one fact that is genuinely its own — *who the client is*.

## The three seams (`app/core/portal.py`)

None of them lets the portal module import `contacts`, or `contacts` import the portal
(CLAUDE.md §6). All three are registered by the contacts module's package `__init__`.

| Seam | Answers | Registered on |
|---|---|---|
| company scope resolver | "which companies does this portal membership see?" | `app/core/scope.py` (#191) |
| portal-user resolver | "which of these users are client logins?" | `app/core/portal.py` |
| **subject provider** | "who is this login for, and attach one" | `app/core/portal.py` |

The subject provider is the new one and the reason the split works. `PortalSubject` is
`(entity_type, id, email, display_name, user_id)` — enough to invite somebody and record it on
their trail, and nothing about how the owning module stores the link.

`list_logins()` (#406) is the fourth method and the register's whole reason for existing: it
enumerates the subjects that already carry a login, and enumerating means reading the owner's
table, which §6 forbids the portal module from doing. It reads through the owner's repository
like `load()`, batches (one statement for the subjects and one per lookup they share, never a
`state` call per row), and returns a `PortalSubjectListing` — the subject plus the clients it
belongs to, because "who at our clients can sign in?" is half-answered by a list of names.

`load()` goes **through the owner's repository**, so the company horizon applies: a membership
scoped to one company group can only ever invite the contacts of its own clients, and anything
else is a 404 like every other read. `for_user()` deliberately does **not** — its one caller is
a portal session ending its own impersonation, where the row *is* the caller, who may have an
empty horizon and would otherwise fail to find itself and silently drop the stop from the trail.

### What deliberately did **not** move

The **horizon** and the **portal-user resolver** stayed in `contacts`. They have to answer
whether or not the portal module is enabled or licensed:

> An entitlement decides whether you may invite someone new. It may never decide whether an
> existing client session stays contained.

A lapsed licence that un-scoped every live client login would be a security incident wearing a
billing event's clothes. `tests/test_entitlements.py::test_portal_invites_are_licensed_but_the_way_out_is_not`
pins it: past the grace window the invite 402s, and the client who already has a login still
signs in and still sees exactly their own company.

## Routes

`/api/v1/portal/logins/{entity_type}/{subject_id}` — `GET` state, `POST` invite/re-enable,
`DELETE` disable, `POST …/resend`, `POST …/impersonate`. Plus `POST /api/v1/portal/impersonation/stop`.

`GET /api/v1/portal/logins` is the **register** (#406) — every client login in the org, one row
each. Declared before the `{entity_type}` routes so a literal path can never be swallowed by a
subject type, and reusing `members.member.write` rather than minting `portal.login.read`: a new
key means a `DefaultsRevision` and a section invisible in every existing org until somebody edits
a role, for a list whose actions are that permission anyway.

Four rules it must not get wrong, and only the last is decided in this module:

* **the horizon narrows it** (#285) — it rides on the provider's repository read, and the count
  is `len()` of the rows, so a restricted admin can never be shown a total the list contradicts;
* **a portal login never reads it** — externality is its own axis (#274), not a permission, and
  `members.member.write` is a key a tenant may grant to any role it edits. 403, not an empty
  list: this is a whole surface, not a panel that can politely be blank;
* **one batched call**, pinned by
  `tests/test_perf_query_budgets.py::test_the_portal_login_register_costs_the_same_however_many_logins`;
* **the order** is by client, then by person — a register is read to answer a question about a
  client, and a login attached to no client at all files last.

`entity_type` is the registered subject type (`contact`). It is in the URL rather than assumed
so the portal module names no other module anywhere, a URL included; an unregistered type is a
404, exactly like an unknown id.

## Permissions

| Key | Guards |
|---|---|
| `members.member.write` | create, invite, resend, disable |
| `portal.login.impersonate` | signing in as the client (`docs/IMPERSONATION.md`) |

**Managing a login is member management, deliberately.** Creating an account, mailing an invite
and switching it off are the same capability that invites a colleague, pointed outward. Minting
`portal.login.manage` would have silently removed the ability from every tenant-defined role
holding member management today, and no reconciler can restore that: a per-key diff cannot tell
"never offered" from "offered and unticked".

`portal.login.impersonate` was `contacts.portal.impersonate` before the split. Stored role
grants and API-key scopes are rewritten in place, once per org, by `@rev:296-portal-module` in
`core/permissions/reconcile.py`. Nobody's access changes — only the spelling.

## Licensing, and the locked button (#137)

`sku="portal"`. The module router carries the standard write gate: past expiry + grace,
mutations answer `402 errors.license_expired` and reads keep working.

**One route is exempt** (`license_exempt` in `core/entitlements/service.py`):
`POST /portal/impersonation/stop`. Gating the way *out* of an impersonation would strand
whoever was inside a client's session the moment a licence lapsed, and an escape hatch is not a
thing anyone should have to buy. It mutates no licensed data — it clears a cookie.

### What the user sees

`/meta/tenant` carries `enabled_modules`, `licensed_modules`, `entitled_modules` and
`deployment`. It rides the payload the app layout already loads, so gating an affordance costs
no second call (`docs/PERFORMANCE.md`), and `/meta/modules` computes the same two lists from the
same helper so a locked control and Instellingen → Modules can never disagree.

The card then renders in one of three states:

| | Card | Invite control |
|---|---|---|
| no `members.member.write` | hidden | — |
| module not enabled, or not entitled | shown | **locked** (`LockedButton` → `UpgradeModal`) |
| usable | shown | the real buttons |

A missing *permission* hides the card; a missing *entitlement* locks the button. That
distinction is the rule, not a detail: a lock is only ever shown for something the org itself
can change. Showing a colleague a padlock they can never open is a worse screen than not showing
the control at all (`docs/UX.md`).

### `UpgradeModal` — one dialog, two futures

`$lib/core/ui/UpgradeModal.svelte` is generic; the portal is simply its first caller. What an
upgrade *means* depends on the deployment, which is why it takes `deployment` rather than
hardcoding a destination:

- **self-hosted** — a licence key, and the destination is real today: Instellingen → Licentie.
  It is instance-owner-only (`users.is_superuser`), so anyone else is told who to ask rather
  than being sent to a screen that will refuse them. A redirect out to the vendor's own portal
  belongs in this same slot, via `upgradeHref`.
- **cloud** — a plan change. Self-service billing from inside the workspace is **not built yet**
  (epic #199 provisions orgs over the instance API), so until `upgradeHref` is passed the dialog
  explains and offers no button. A link that goes nowhere is a broken control (#253); the
  component never renders a CTA it cannot honour.

**Both halves of the dialog follow `deployment`, not just the CTA.** When there is no button
there is a line saying who to ask instead, and it was briefly shared between the two postures —
which on cloud told the tenant to "ask whoever administers this instance", a person who does not
exist on their side of a hosted box. `body_no_route` is the self-hosted line and
`body_no_route_cloud` names the subscription; a caller who adds a third posture adds its own.
The rule generalises past this dialog: once a screen branches on `deployment` at all, every
sentence on it that names *who fixes this* has to branch too, or the copy contradicts itself
halfway down.

## Where a login is *managed* — and where it is *found* (#406)

Two surfaces, and they answer different questions on purpose.

**The contact's own page** carries `PortalCard`: this person, this login, invite / resend /
disable / sign in as. It is where a login is created, because a login is something you give a
particular person.

**Instellingen → Gebruikers → Klantlogins** is the *register*: every client login the agency
has, with the client it belongs to and the status. Before it existed a login was reachable from
exactly one place — the contact — so nobody could say how many existed without opening every
contact, a client's employee who left kept a live login until somebody remembered which row it
was, and an invite nobody ever used was invisible.

It sits beside the team, not in a screen of its own, for the reason SSO sits under Team &
toegang rather than under Integraties (#378): what it is about is *who may sign in*. And it is a
register with the access actions on it, never a second editor — every row links to the person's
own record, which stays the one place a person is edited.

Three things about the web half:

* **The gates are `loadPortalCard`'s, reused rather than re-derived** (`loadPortalLogins`):
  permission → does this workspace run `portal` → is it still entitled. One difference is
  deliberate: a workspace that does **not run** the module gets no section at all, where a
  record page still draws a locked card. A card is an affordance where somebody is already
  looking; a section that exists only to say "not for you" is a worse screen than none.
* **Enabled but unentitled is locked, not hidden** (#137) — an entitlement is something the
  agency can change, so the affordance stays and `UpgradeModal` says how.
* **Each action is gated on the key the call actually makes** (#310): managing the login is the
  section's own `members.member.write`, and *Inloggen als* is `portal.login.impersonate`, which
  most staff will not hold. The subject arrives in the **form body** rather than in the route,
  which is why `portalActions` takes one `subject(event)` resolver that may be async — a record
  page reads its route params, the register reads the pressed row, and a request body may only
  be read once.

## The numbers are the client's; the machinery is the agency's (#446–#449)

The first client review of the portal came back with one sentence under ten points: *alles wat
verwijst naar interne processen, leveranciers of medewerkers achter de schermen hoort niet thuis
in het klantportal.* Four of the ten were the same rule seen from four screens, and the fix for
each is on the **API**, keyed on `ctx.is_portal` (#274) — never on a permission that happens to
exclude clients, and never on `!isPortal` in a component alone (§15: the web mirrors, the API is
the boundary).

* **A supplier's name is not the client's business** (#446). `SourceMetrics.label` is what *this
  reader* calls a source: `None` for staff (the web prints the product name) and always set for
  a portal login — the tenant's own client-facing name from Instellingen → Marketing
  (`marketing_settings.portal_source_labels`), else a vendor-free default for the keyed sources
  (`marketing.source.portal.<source>`: "Zoekmachineposities", "AI-zichtbaarheid") and the product
  name for a Google source, which is the client's own account. "Breik. Analytics" is one tenant's
  word and lives in that setting, never in code (§2, rule 4). Resolved in `_source_metrics`, so
  the widget, the marketing tab and an MCP client answer with the same word.
* **A link into the supplier's console is #253's control that always refuses** (#447), printed
  beside the supplier's name. `deep_link` is empty for a portal reader on the source row *and* on
  every drill-down, and a drill-down that could not be read answers one neutral reason
  (`marketing.portal_unavailable`) — "reconnect" and "ask your administrator" are sentences for
  the agency, and every stored reason names either the vendor or the fix.
* **Whose Google grant feeds the numbers is a working-surface fact** (#448). `connection_owner`
  is `None` for a portal reader, exactly as `connections` already were (#411).
* **A budget is what the agency agreed with itself about the work** (#449). `projects/router._read`
  blanks `budget_hours`, `budget_amount`, `hours` and `budget_sources` on every project read for a
  portal login, the company-hub panel omits the same fields, and a task row's `allocated_minutes`
  is `None` (its burn already was, gated on `time.entry.read`). The web draws no column, block,
  pill or chip for what the API blanks — a dash headed "Budget" is a question the client should
  not be holding. `budget_watch` had made this decision for the *mails* long before the screens
  caught up.

`tests/test_marketing_portal.py` and `tests/test_projects_portal.py` pin all four against a real
portal session beside a staff one on the same endpoints.

## The client's board: what is asked of them, then what was written for them (#450–#453)

The same review found the portal homepage squeezing every widget into a 50 % column beside an
empty card, printing a report as one grey paragraph, and offering no tasks at all — while a task
*assigned* to the client's contact (#273) was invisible to that contact and read "Contactpersoon
(Contactpersoon)" on their own page. Four rules came out of it.

* **"Mine" for a client is a question about a contact, not a user** (#453).
  `TaskService._portal_contact_id` resolves the contact behind a portal session through the
  portal-subject seam (`for_user`, so tasks names no other module's table), and
  `_mine_condition` is the one predicate `/tasks/mine`, `/tasks/dashboard-mine` and its bucket
  counts share — a page and its totals must agree about whose tasks these are. A portal login
  behind no contact matches nothing, which is the honest answer rather than the org's list.
* **Assigning a contact makes the task visible to the client**, recorded on the trail as the field
  edit it is (`_contact_assignee_implies_visible`, create and update alike), and every reader gets
  the contact's *name* (`TaskRead.assignee_contact_name`, resolved in `_list_items` with one
  org-scoped query per page) — the browser used to look it up through `/contacts?company_id=`,
  which is exactly the endpoint a client cannot read. The create dialog offers the client's
  contacts when a client is pinned, through the same `TaskAssigneePicker` the task page draws.
* **The client's board is one column, top down** (#451): what is asked of them (`tasks.portal`,
  position 5), the latest report (10), the live dashboard (15). Every portal widget is a `lg`
  tile, and `spec.size` had been applied nowhere but the gallery. A layout stored with two columns
  folds into one rather than dropping a column. Staff My Day is unchanged.
* **A report on the homepage reads as the report's front page** (#452): period, publication date,
  the summary as paragraphs in body colour, each section under its own heading, the two ways in.
  The prompt deliberately asks for prose without markdown, so the widget gives the document its
  shape rather than rendering markup it does not have.

`tests/test_tasks_contact_assignee.py` pins the assignment half against a real portal session.

* **The contact is told, by mail, only if they can open the link** (#454). `tasks.assigned_contact`
  is a tenant-rewordable kind (`docs/EMAIL.md`), queued from the assignment inside `release_db`
  and sent by the worker with its own session — never inside the transaction that made the
  assignment, and never to a contact without an **active** portal login: a link to a login they
  do not have is #253's control that always refuses, printed in an inbox. Whether they hold one
  is asked through the portal-subject seam, so `tasks` still names no other module's table.
  `tests/test_tasks_contact_mail.py` pins the queue, the gate and the override.

## Adding a second kind of subject

1. Implement `PortalSubjectProvider` in the module that owns the row (`load`, `for_user`,
   `attach`), with `entity_type` set to the same string `AuditableMixin` registers.
2. `register_portal_subject_provider(...)` in that module's package `__init__` — there, not in
   the portal module, so the dependency points the one direction §6 allows.
3. Render `PortalCard` on its detail page, spread `portalActions({entityType, …})` into the
   route's actions, and call `loadPortalCard` in its load.

No change to the portal module, core, or the routes.
