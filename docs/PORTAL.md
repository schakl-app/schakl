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

## Adding a second kind of subject

1. Implement `PortalSubjectProvider` in the module that owns the row (`load`, `for_user`,
   `attach`), with `entity_type` set to the same string `AuditableMixin` registers.
2. `register_portal_subject_provider(...)` in that module's package `__init__` — there, not in
   the portal module, so the dependency points the one direction §6 allows.
3. Render `PortalCard` on its detail page, spread `portalActions({entityType, …})` into the
   route's actions, and call `loadPortalCard` in its load.

No change to the portal module, core, or the routes.
