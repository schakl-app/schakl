# CLAUDE.md — Project Constitution

> This file is the source of truth. Read it fully at the start of every session.
> If a request conflicts with the **Golden Rules**, stop and flag it instead of complying.

## 1. What we're building

A **multi-tenant, modular, white-label agency operations platform**. One codebase runs
many agencies (tenants). Each tenant manages **companies** (their clients) and attaches
things to them: **people/contacts, websites, hosting accounts, projects, retainers, deals,
time entries**. The platform is a web app (SSR) that is also installable as a **PWA**.
Primary language is **Dutch**; full internationalization with English as the second locale
and trivial addition of more.

Internal codename: `schakl`, shown to people as the product name **schakl.** (with the dot).
The `schakl` form — no dot — is the only one used in code, urls, package names, env vars and
other identifiers; the dot appears solely when the official product name is displayed. The
**brand shown to users is per-tenant** and never hardcoded, so this name is never the tenant's.

## 2. Golden Rules (non-negotiable)

1. **Tenant isolation.** Every domain table has `org_id`. Every data access is scoped to
   the current tenant through the shared tenancy layer. Never write a query that can return
   another tenant's rows. Postgres RLS is enabled as defence-in-depth.
2. **No hardcoded user-facing text.** Every user-visible string goes through i18n and is
   added to `messages/en.json` (source) **and** `messages/nl.json` (required) in the same
   change. `nl` must never be left partial — it's the default UI language.
3. **Everything is a module.** Each domain lives in a self-contained module that
   self-registers. Modules never import each other's internals — only through the registry
   and published interfaces.
4. **Branding is runtime, per-tenant.** No hardcoded logos, colors, product name, or domain.
5. **Schema only via Alembic migrations.** Never hand-edit the database or models without a
   migration.
6. **The API is the only data path.** The web app never talks to the database directly.
7. **Build in phases, stop at each gate.** Implement one phase, run migrations + tests, then
   summarize and wait for review. Do not build ahead.

## 3. Tech stack (locked — do not substitute without asking)

| Layer         | Choice |
|---------------|--------|
| Web app       | SvelteKit (SSR) + `@vite-pwa/sveltekit` · TailwindCSS · Bits UI / shadcn-svelte |
| i18n (web)    | Paraglide JS (inlang) — flat JSON message catalogs, type-safe, tree-shaken |
| API           | FastAPI · Pydantic v2 · SQLAlchemy 2.0 · Alembic → auto OpenAPI |
| Documents     | Jinja → HTML → **WeasyPrint** (`invoicing/render/`): the page the browser previews *is* the page the PDF prints, and a tenant may bring their own design (sandboxed Jinja, no network) — `docs/INVOICING.md` |
| Payments      | Provider-agnostic seam in `app/core/payments/` (epic #269): a `PaymentProvider` protocol, a per-provider account resolver, and the `{org}.{account}.{secret}` callback token a provider's unauthenticated POST names its tenant by (the Google channel-token pattern). `mollie` is the first implementation and Stripe/Adyen are a package, not a refactor. **A webhook body is a hint, never a fact**: only the id it names is read, and status, amount and mode come from an authenticated re-fetch with the tenant's own credential. A confirmed payment writes an ordinary `InvoicePayment` row, so invoicing stays the single answer to "what has been paid" — `docs/PAYMENTS.md`, `docs/MOLLIE.md` |
| Typed client  | `openapi-typescript` client generated from the API's OpenAPI spec |
| Database      | PostgreSQL (with Row-Level Security) |
| Jobs & cache  | Redis + ARQ |
| Auth          | App-native at the API: FastAPI Users (local username/password, verification, reset) + 2FA on local login (TOTP + backup codes, optional SMS via instance gateway; org-admin reset — docs/TWOFACTOR.md) + Authlib (OIDC relying-party, configured **per org in the DB** — Instellingen → SSO, #76; encrypted secret, runtime toggles, `SCHAKL_FORCE_LOCAL_LOGIN` break-glass) · Google OAuth for Workspace scopes |
| File storage  | Pluggable backend (named volume · S3-compatible) behind `app/core/storage/`. A file row is **not** its bytes: `file_blobs` holds one object per distinct sha256 **per org**, so the signature logo on 500 e-mails is one object — and therefore no single row may ever delete one. Call `service.drop_file`, never `storage_for(...).delete(key)`; a nightly cron folds pre-dedup rows and reclaims what nothing references — `docs/STORAGE.md` |
| Received mail | HTML → markdown at ingest (`app/core/htmlmd.py`), stored beside the plain text as `interactions.body_markdown` and **only** when the message had an HTML part: text a *sender* wrote is not our markdown, so a plain-text mail keeps rendering as plain text. Its `cid:` images become files marked `content_id` (body content, not attachments) and the body's marker becomes `file:<uuid>`, resolved by the renderer; a remote `<img>` is dropped — a tracking pixel is an image — `docs/GOOGLE.md` |
| Infra         | Docker Compose · Traefik · deployed on Hetzner · Cloudflare Zero Trust. **A redeploy is not an outage**: the API rolls `start-first` on two replicas because "one migration at a time" is now stated as a Postgres advisory lock (`app/core/migrations.py`) rather than as `replicas: 1` — `docs/DEPLOY.md` |
| MCP / AI       | MCP server over Streamable HTTP (OAuth 2.1 resource server) via the official Python MCP SDK / FastMCP; mounted on the API app; tools contributed per module, read-first · every AI feature goes through one core (`app/core/ai/`, `docs/AI.md`): per-tenant provider + encrypted key, and **every in-request model call wrapped in `ctx.release_db()`** — §11's pool-drain is worst here, because a tool loop holds the connection for tens of seconds. Speech-to-text is its own credential (`docs/VOICE.md`): Anthropic has no transcription endpoint and is the default provider, so "reuse the chat provider" configures nothing |

Ship these as separate containers in one Compose file: `web`, `api`, `worker`, `db`, `redis`, `traefik`.

## 4. Repository layout (monorepo)

```
apps/
  api/app/
    core/          # config, db session, tenancy, auth, i18n, module registry, RLS helpers
    modules/
      companies/   # models.py schemas.py service.py router.py panels.py impex.py migrations/
      contacts/
      websites/
      hosting/
      projects/
      time/
      leave/        # employee PTO / leave (see §14)
      ...
    main.py        # discovers enabled modules and mounts their routers
  web/src/
    lib/core/      # api client, tenant/theme loader, i18n runtime, module + nav registry
    lib/modules/
      companies/   # components, CompanyPanel(s), nav items, message namespace
      ...
    routes/        # thin route files that delegate into modules
    paraglide/     # generated (do not edit by hand)
messages/          # en.json (SOURCE), nl.json (required, default UI lang) — flat, namespaced keys
infra/             # compose files, traefik config, seed scripts
scripts/           # i18n:check, i18n:sync, gen:client
```

## 5. Multi-tenancy

- `orgs` (tenants), `users`, `memberships` (user↔org with a `role`: owner/admin/member/client).
- Every domain row: `org_id NOT NULL` FK → `orgs.id`.
- A request resolves `current_org` from the **hostname** (subdomain or custom domain, see
  white-label) → mapped to an org, then verified against the user's membership.
- All ORM access goes through a base repository / session dependency that injects the
  `org_id` filter automatically. Postgres RLS policies enforce the same at the DB layer.
- **Never** expose a raw `id` lookup that isn't tenant-scoped.

**Deployment model: build multi-tenant, deploy single-tenant.** Each agency **self-hosts**
its own instance and creates **one org** via the first-run wizard (`/setup`) — so day-to-day
it runs as a single tenant, and the agency's clients are `companies` (data), not tenants.
Keep `org_id` + RLS on every table anyway: it's near-free now and is the only thing that
lets the *same code* run a future multi-org **cloud** version with the tenant resolved by
hostname. Don't take shortcuts that assume one org.

The multi-org posture exists: `SCHAKL_DEPLOYMENT=cloud` (epic #199, **business-licensed** —
`apps/api/app/core/cloud/`, `apps/web/src/routes/(cloud)/`, see `docs/CLOUD.md`). It moves
the instance console to the apex host (no org resolves there), provisions orgs over an
instance-API-key API with per-org plans (trial / standard / unlimited), requires an
**org-issued service PIN** before the instance owner may touch tenant data, offers
instance-provided e-mail as a per-org choice, and terminates TLS itself (wildcard origin
cert for subdomains, Let's Encrypt for verified CNAME domains). Google and AI credentials
stay bring-your-own per org.

- **Hostname resolution is strict**: a verified custom domain (`orgs.custom_domain`) or
  `<slug>.<base_domain>` — an unknown host is an explicit error, never "the only org".
- **A session belongs to one org, and the token says which** (`app/core/auth/backend.py`).
  `users` is instance-level and the password check is tenant-blind, so "the credentials are
  right" and "the credentials are right *here*" were the same sentence only because a
  self-hosted box has one org. On any multi-org instance the login route on org A's hostname
  minted a real session for a member of org B. Both ends now agree: the **account lookup** is
  narrowed to the request's org (`UserManager.get_by_email`, so login, password-reset and
  request-verify all answer as if the address did not exist — no cross-tenant enumeration),
  every **mint site** stamps the resolved org into the JWT (`/auth/login`, the 2FA challenge
  *and* its redemption, the OIDC callback, the impersonation handoff), and `require_context`
  refuses anything whose claim is not this org — **401, not 403**: it is an authentication
  answer, and the membership check is a different question that passing would not fix. A host
  that resolves to no org (the cloud console's apex) mints an org-less session on purpose: it
  reaches the instance surface and no tenant data at all, which is the same rule, not an
  exception to it. A missing claim therefore always fails closed, which includes tokens
  predating the claim — a session-format change costs one re-login and is worth stating in the
  release notes.
- **Org lifecycle & instance administration** (issue #26) live in `app/core/instance/`:
  the one sanctioned unscoped crossing (`repo.py`, for global slug/domain uniqueness), the
  audit trail, org lifecycle, export/import, and time-boxed impersonation. Disabled by default
  (`SCHAKL_INSTANCE_ADMIN_ENABLED`), and gated on **two principals** — a third authorization
  axis, neither RLS nor org RBAC. An **owner** (`users.is_superuser`, distinct from an org
  `owner`) holds everything implicitly; an **admin** (`instance_admins`) holds an explicit
  capability set from the code-defined catalog in `capabilities.py`. Every `/api/v1/instance`
  route declares its capability and a missing declaration is a build break
  (`tests/test_instance_deny_by_default.py`), exactly as §15 requires of tenant routes.
  **Delegation is owner-only and deliberately not a capability**: an admin who could grant
  `instance.impersonate` to themselves would be an owner with extra steps. The last active
  owner can never be removed (`errors.last_instance_owner`) — a box nobody can administer is
  unrecoverable, and an admin cannot promote anyone.

## 6. Module pattern (how to add a domain)

An **API module** is a package under `apps/api/app/modules/<name>/` exposing:
- `models.py` — SQLAlchemy models, all with `org_id`, all inheriting the shared `Base`.
- `schemas.py` — Pydantic request/response models.
- `service.py` — business logic (no DB access outside the tenant-scoped repository).
- `router.py` — REST endpoints under `/api/v1/<name>`, mounted by `main.py`.
- `permissions.py` — the `PermissionSpec`s this module introduces, declared on its
  `ModuleDescriptor` (see §15). Core holds no module permission list.
- `panels.py` — optional: declares what this module attaches to a **company** (title +
  data provider) so the company detail view can compose it. This is the modular hub.
- `impex.py` — optional: the entity's spreadsheet import/export shape, and any columns this
  module contributes to *another* module's entity (see §17).
- `email_templates` — optional: the outgoing mails this module lets the tenant reword
  (`EmailTemplateKind`, `docs/EMAIL.md`). A mail the agency's **client** reads is theirs to
  write: core declares only the auth pair and holds no module list, keys are namespaced
  (`invoicing.invoice`) and asserted at mount, and a missing override means the built-in text —
  so contributing one adds no schema and changes nothing until a tenant types in the box.
- `mcp.py` — optional: the MCP tools/resources this module contributes (e.g.
  `companies.find`, `companies.recent_projects`), registered onto the MCP surface alongside
  the router. Read-only by default; each tool goes through the tenant-scoped service layer.
- entities that should accept **tenant-defined custom attributes** use the shared
  `CustomizableMixin` (adds a `custom` JSONB column and registers the `entity_type` with the
  custom-fields core — see §13).
- `migrations/` — Alembic revisions owned by the module.
- registers itself into the **module registry** (name, router, models, panels, permissions,
  mcp tools, cron jobs, i18n namespace).
- **authorization is deny-by-default** (§15): every route declares a permission with
  `require_permission(...)`, or an explicit `no_permission_required("reason")`. A route that
  declares neither is a build break — `tests/test_rbac_deny_by_default.py` calls every
  `/api/v1` operation as a member holding nothing and demands a `403`.
- **cross-module reactions** go through the tiny in-process event bus
  (`app/core/events.py`), never via imports of another module's internals: the owning
  module's service `emit`s (today only `company.created` / `company.status_changed`) and
  interested modules `subscribe` a handler in their package `__init__`. Handlers run in the
  emitter's request transaction, so an event and its side effects commit atomically.
- **background/cron work** is contributed as ARQ `cron_jobs` on the `ModuleDescriptor`; the
  worker collects them from enabled modules. Jobs bind tenant context per org via
  `app/core/jobs.run_per_org` (RLS GUC per org, one transaction per org).

A **web module** mirrors it under `apps/web/src/lib/modules/<name>/`:
- components, a `CompanyPanel` (renders that module's data on a company page),
- nav items it contributes, and its message namespace (`<name>.*` keys).

`main.py` and the web `core` registry load only the **modules enabled for the tenant**.

### The "attach to company" model
Companies are the hub. `contacts`, `websites`, `hosting`, `projects`, etc. each carry
`company_id` (+ `org_id`). The **company detail page** renders panels contributed by every
enabled module via the registry — so adding a new attachable type (e.g. `domains`,
`ssl_certs`) is just adding a module, no edits to the company page. For cross-links that
aren't a simple FK, use a generic `relations(org_id, from_type, from_id, to_type, to_id)`
table.

## 7. White-label / theming

Per-org settings drive branding at runtime — no rebuild:
`org_settings(org_id, brand_name, logo_url, favicon_url, primary_color, accent_color,
default_locale, enabled_modules[])`.
The web app loads the tenant theme on first render and applies it via CSS custom properties.
Emails and generated PDFs use the same tenant branding — every outgoing mail is wrapped in
the tenant's branded HTML chrome at the send seam (`docs/EMAIL.md` holds the architecture
and the HTML e-mail template rules). Tenant resolution: **verified**
`orgs.custom_domain` or `<slug>.SCHAKL_BASE_DOMAIN` → org. The custom domain lives on
`orgs`, not `org_settings`: resolution runs *before* RLS is bound, so it may only read
tables without RLS — and a claimed domain routes traffic only after DNS TXT verification.

## 8. Internationalization (first-class)

- **Source locale = English (`en`)** — the canonical file everyone (and Claude) translates
  from. **Dutch (`nl`) is a required, always-complete locale and the default display
  language** (per-tenant configurable). Source locale ≠ default UI language: the app ships
  showing Dutch, but `en.json` is the source of truth for keys. Adding a locale = adding one
  JSON file; no code changes needed.
- **Web:** Paraglide JS (inlang). Messages live in `messages/<locale>.json` as **flat,
  namespaced keys** (`companies.title`, `time.timer.start`, `common.save`). Use `m.key()`;
  never concatenate strings; use ICU params for interpolation and plurals.
- **API:** email/notification/validation/PDF strings live in matching JSON catalogs keyed
  identically; locale comes from the user (fallback: org default → `nl`).
- **Translation workflow (make it Claude/human friendly):**
  - `en.json` is the source of truth. `nl.json` and every other locale mirror its keys exactly.
  - `scripts/i18n:check` fails CI if keys are missing/extra across locales (`nl` must be full).
  - Keys are descriptive and grouped by module, so a whole file can be translated in one pass.
  - Rule: any change that adds a string updates **all** locale files (incl. `nl`) in the same commit.
- **Formatting:** dates, numbers, currency via `Intl`, currency `EUR`. Timezone is
  **per-tenant** (`org_settings.timezone`, an IANA name; instance fallback `Europe/Amsterdam`):
  the web renders event timestamps in it (resolved via `getTimeZone()` — server AsyncLocalStorage
  + `<html data-timezone>` on the client, mirroring the locale plumbing), and the per-org cron
  (timesheet nudges, holiday top-up) reasons about the local calendar in it via
  `app.core.timezone.org_zoneinfo`. Stored instants stay `TIMESTAMPTZ`/UTC; date-only values stay
  wall-clock UTC; leave `TIME` stays naive (§14). No per-user override yet — the resolution seam
  is in place for one.
- **No module keeps its own clock.** `ZoneInfo("Europe/Amsterdam")` anywhere but
  `config.default_timezone` is a build break in spirit: three modules each grew a private `_TZ`
  and quietly handed every tenant Amsterdam's midnight — project budget periods rolled over on
  the wrong day, task recurrence and reminders called the wrong day "today", and digests fired an
  hour early in Lisbon and an hour late in Warsaw. A function that reasons about a wall clock
  **takes the zone (or the local day) as an argument**; whoever has the org resolves it once via
  `org_zoneinfo` / `org_today`. The instance default is read through `resolve_zoneinfo(None)`, per
  call rather than at import, so configuration is never frozen into a module constant. The same
  rule binds the tests: `tests/conftest.org_today()` is the one "today" an expectation may use —
  a test computing it from `date.today()` or UTC agrees with the app only on a developer's
  machine and fails on CI, which runs in UTC. A test may still *name* a zone when the zone is its
  subject (a DST boundary); that is an input, not an assumption.

## 9. Conventions

- REST: plural nouns, `/api/v1/<module>/<resource>`; cursor or page/limit pagination;
  consistent error envelope `{ error: { code, message, fields? } }` (message is an i18n key).
- Auth: a single FastAPI dependency yields `(current_user, current_org, role)`.
- Tests: `pytest` (API, incl. a tenant-isolation test per module) + Playwright (web smoke).
- Migrations: one per change, named `<module>_<verb>_<noun>`.
- Commits: small, scoped, conventional (`feat(time): add weekly timesheet grid`).
- **Definition of done** for a feature: migration written, endpoints + tenant scoping,
  **every route declaring a permission** (§15) and its `PermissionSpec`s on the module
  descriptor with `en`+`nl` labels, web UI (**every entity-reference picker offers inline-create →
  full dialog → auto-select**, and **every list screen ends in the shared pager**, `docs/UX.md`),
  `nl.json` + `en.json` keys, test for tenant
  isolation, **a mutable entity records its changes to the activity log and its detail view
  renders the trail** (§16), docs/OpenAPI updated. **Performance is part of done, not a
  follow-up**: a list endpoint exposes `count=false` and skips whatever the caller opts out of, a
  row carries only what its screen draws, aggregates are computed in SQL with the company horizon
  carried, every unbounded read is capped, section-shared lookups live in the section's layout
  load, and the whole thing lands with a `count_queries` budget test (`docs/PERFORMANCE.md`).
- **A list screen pages; it never shows a prefix of itself.** The old shape — `limit: 200,
  offset: 0` and a sentence apologising for it — made a tenant who outgrew the cap read a sample
  as the whole answer, with row 201 reachable only by guessing a search term. One contract now
  covers every index (`$lib/core/table/paging.ts`, `core/ui/Pagination.svelte`): **the URL is the
  view** (`?page=` / `?size=`, so the back button lands where the user left and a page is
  shareable — hence `<a href>`, never a click handler), **the load resolves and the API applies**
  (`resolvePaging(event.url, pref)` → `limit`/`offset`; a filter the API cannot express is a
  missing query parameter, never a licence to narrow the slice in the browser), **every filter,
  search and sort drops the page** (`resetPage`), and **the size is a saved personal default, not
  state** (`TablePref.page_size`, 50 by default, beside the column layout — the *page* stays in
  the URL, or two tabs fight over one number). A group count inside a page counts the page, so a
  sectioned list says so. The narrow exceptions — a grouped inventory, a report whose subtotals
  span the whole set, an approval queue meant to be emptied — are named in `docs/PERFORMANCE.md`.

## 10. Phased plan (build gates)

- **P0 Foundation** *(do first — cross-cutting)*: monorepo + Docker Compose; FastAPI core
  (tenancy + RLS, i18n catalogs, module registry, and **auth**: FastAPI Users local
  username/password by default + Authlib OIDC federation that, when enabled, disables local
  login); SvelteKit + PWA shell with tenant theming + Paraglide; the `companies` module
  end-to-end as the reference
  implementation; `i18n:check` and client-gen scripts; CI.
- **P1 MVP**: `contacts`, `tasks`, `time` (timer + manual + weekly timesheet), dashboard /
  My Day, and the **per-tenant custom-fields framework** (§13), proved on `companies`/`contacts`.
- **P2 Agency core**: `projects`/retainers + budgets, `pipeline`/deals, `leave`/PTO
  (see §14), reporting.
- **P3 Google Workspace**: OAuth, calendar sync, Gmail logging, Drive linking, contacts sync.
- **P4 Automation & public API**: documented public API, webhooks, n8n, scheduled PDF
  reports, and the **MCP server** (read-first tools per module, starting with `companies`).
- **Attachable assets** (`websites`, `hosting`) slot in as modules — target P2, but the
  module + company-panel pattern must be proven in P0.
- **`cloudflare`** (epic #278, `docs/CLOUDFLARE.md`) is what finally puts a mechanism behind
  `Domain.status = redirect`: a Redirect Rule schakl owns on the client's own Cloudflare zone,
  plus DNS view/export and Pages linking. Two rules generalise beyond it. The credential is a
  **row, not a per-org setting** — an agency holds its own account and its clients bring theirs,
  and the same apex can legally exist in two of them, so nothing ever picks an account for you.
  And an integration that mirrors outside state stores **what it decided** and **what it last
  observed** in separate columns, so "somebody changed this in the provider's dashboard" is
  expressible at all: a reconcile reports drift instead of silently overwriting it. The
  registrar half is now **`oxxa`** (#296, `docs/OXXA.md`): the register sync, the nameserver
  write-back that finishes "Connect to Cloudflare", and the `app/core/registrar/` seam a second
  registrar plugs into. Written from OXXA's official API documentation — §11 bans writing an
  integration *from memory*, not from a document — but **never exercised against a live
  credential**, so `docs/OXXA.md` §1 carries the checklist to run the day one arrives, and every
  parse there is defensive until it has.
- **What the registers are actually *for* is deciding who pays** (#298, `docs/INVOICING.md`). An
  agency's domain list mixes names it renews for the client with names the client registered
  themselves and merely asked us to point somewhere, and only a register can tell them apart —
  a zone cannot: Cloudflare answers DNS for plenty of domains it does not hold. So
  `Domain.invoiceable` is three-state and never read alone: `TRUE`/`FALSE` are somebody's
  decision, `NULL` is *follow the register*. Three rules hold it up. **A credential is not an
  authority** — only a register that has actually *answered* (`registrar_synced_at`, not a stored
  token, and not the zone sync) may narrow what gets invoiced, which is also what makes this safe
  to ship into an instance already invoicing domains: with nothing read, every undecided domain
  bills exactly as it did. **`domains` may not name the registers** (§6), so each contributes its
  own two SQL clauses through `app/core/registrar/presence.py` and core only composes them — the
  `app/core/directory.py` seam pattern, applied to a predicate instead of a row. And **the
  resolution is one clause**, taken by the renewal cron, the list filter, the outstanding picker
  and the per-row read alike, so a screen and the cron can never disagree about which domains
  bill. Reported wherever it changes an answer, never silently applied.
- **The client portal is a module, and what it sells is not what it enforces** (#193/#296,
  `docs/PORTAL.md`). Everything the portal does happens on a *contact's* page, which is why it
  started life inside `contacts` and why that was wrong: it is a product the agency buys
  (`sku="portal"`), it has its own lifecycle, and its subject need not be a contact at all. So
  `app/modules/portal/` owns the invite, the disable, the impersonation and the screen, and
  reaches the person through a third seam on `app/core/portal.py` — a `PortalSubjectProvider`
  registered by whoever owns that row, carrying only `(entity_type, id, email, name, user_id)`.
  Two consequences are load-bearing. **The horizon and the "is this a client login?" resolver
  stayed in `contacts` on purpose**: they must answer whether or not this module is enabled or
  licensed, because an entitlement decides whether you may invite someone *new* and may never
  decide whether an existing client session stays contained — a lapsed licence that un-scoped
  live logins would be a security incident wearing a billing event's clothes. And **a whole-router
  write gate needs exactly one exemption** (`license_exempt`): ending your own impersonation. It
  mutates no licensed data, and gating the way out would strand whoever was inside a client's
  session the moment a key expired.
- **A missing permission hides a control; a missing entitlement locks it** (#137, `docs/UX.md`).
  Both refuse, so it is tempting to render them the same way — but only one of them is something
  the org can *change*. A padlock a colleague can never open is a worse screen than no control at
  all, while a paid module the agency has simply not bought is how anyone learns it exists. Hence
  `LockedButton` → `UpgradeModal`, generic and stated once: `deployment` decides what an upgrade
  *means* (a licence key on self-host, where Instellingen → Licentie is a real destination for the
  instance owner and nobody else; a plan change on cloud, where in-app billing does not exist yet
  and so the dialog explains rather than offering a button that goes nowhere — #253's "a link that
  always refuses is a broken control"). The lists it reads (`licensed_modules`, `entitled_modules`,
  `deployment`) ride `/meta/tenant`, which the app layout already loads, and come from the same
  helper `/meta/modules` uses so a locked control and Instellingen → Modules can never disagree.
- **A report is a record, and the voice it is written in is the tenant's** (#300,
  `docs/REPORTING.md`). The monthly client report is `reporting` — its own module and `sku`,
  because a document has a lifecycle (drafted → reviewed → published → sent), an audience and a
  commercial boundary that a live dashboard does not; a tenant licences `marketing` without it.
  `reports.data_snapshot` freezes **every number the document prints**, which is what makes it
  reopenable, makes prose and tables agree by construction rather than by both re-querying, and —
  with `UNIQUE (org, company, audience, period_start)` — makes a re-run update a document instead
  of mailing a client a second copy. **Sections come from the registry** (`report_sections`, the
  panels pattern applied to documents), so `marketing` owns the traffic/rankings/audit half and
  reporting names no module. **The prompt is three layers and they must not fuse**: product
  invariants are code, the agency's editorial voice is a `report_tones` row, and what is true
  about one client is a `report_profiles` row that reaches the model **inside the JSON, never
  appended to the prompt** — a house style compiled into `prompts.py` is a tenant's decision we
  took for them, and a client profile concatenated into the instructions is obeyed. A banned
  phrase is *checked* after generation, not merely requested. **Review is the default and
  auto-send is a per-client setting**: prose a model wrote leaving under the agency's brand
  unread is not a thing to arrive at by not choosing. Externality follows §15/#266 —
  `Report.__portal_horizon_clause__` (own companies, never internal, never unpublished) lives on
  the model because `GET /files` declares no permission and `entity_visible` is its only gate.
  The renderer is shared with invoicing (`app/core/documents/`) rather than copied, and charts
  are inline SVG because the engine's fetcher answers `data:` and nothing else. `marketing`
  borrows the latest published report's paragraph per section through `app/core/narratives.py`,
  so a dashboard stops being a table on the other twenty-nine days of the month.
- **Collecting money is three rules that outlive the provider** (epic #269, `docs/PAYMENTS.md` +
  `docs/MOLLIE.md`). #267 asked for Mollie and argued *against* an abstraction, since no second
  provider was on the roadmap; the owner reversed that, because the issue was right about
  *methods* and wrong about *providers* — Stripe and Adyen are ordinary asks from an agency with
  non-EU clients, and the seam costs one file today against a rewrite of the settle path at the
  exact moment a live tenant depends on the first one. None of the three rules that came out of
  it is about Mollie. **A webhook body is a hint, never a fact: the authenticated re-fetch is the
  authentication.** Mollie posts one unsigned form field and documents that this is safe *because*
  you re-fetch; a provider that posts a whole signed event is no different, because a signature
  proves who sent a message and not that the message is still true. So `verify_webhook` is an
  extra gate and never *the* gate, and `handle_webhook` runs five in one order — the token names
  the tenant, RLS is bound before anything is read, the secret is compared in constant time (a
  mismatch is a bare 404, never a 401 that would confirm the account), the provider gets its
  optional signature check, and only then is the body read for ids and nothing else. **An
  idempotency guarantee that lives in application code loses the race the database would have
  won**: a provider retries until it gets a 200 — ten times over 26 hours — so two deliveries and
  an hourly reconcile cron are in flight against each other, and "have we settled this yet?"
  followed by an insert leaves a window every retry enters. `SELECT … FOR UPDATE` on the intent
  makes the common case cheap; the partial unique index on `invoice_payments (org_id, intent_id)`
  makes the uncommon case *impossible* rather than unlikely, including across two API replicas
  that share no memory. And **an expired licence makes a module read-only; it does not make the
  agency's takings disappear** — the callback is the one route carrying `license_exempt`, because
  a 402 there would drop money that has already left someone's bank account and no retry would
  ever fix it (the provider's retries would 402 too). Gate what the agency *does*; never gate the
  recording of what has already happened to them.

## 11. Working agreement (for Claude Code)

- Start each phase in **plan mode**; propose the plan and wait for approval before coding.
- **Read `docs/UX.md` before building or changing any screen** — it records the product's
  design language (mobile-first, use-vs-edit modes, European dates, template patterns,
  where admin config lives) and the UX mistakes already corrected once. One of them is
  enforced rather than remembered: every `use:enhance`d form that the user types into states
  what happens to it on success — `busy.keep()` to edit, `busy.clear()` to start something new
  — because inheriting SvelteKit's default reset blanks the field the user just saved.
  `pnpm forms:check` (CI + pre-commit) fails a form that says neither.
- **Performance and lean code are first-class requirements.** Slow-feeling pages are bugs.
  Keep SSR loads minimal (shared lookups in layout loads, `meta=false`/`count=false` on
  pickers, no redundant API calls or queries), prefer fixing the data path over adding
  libraries, and when a page feels slow, count its API calls/queries before writing code.
  **Read `docs/PERFORMANCE.md`** — the data-path rules and the per-screen checklist — and **pin
  any perf fix with a query-count test**, because the shape it fixes is invisible in the JSON:
  an endpoint that is one grouped query at three rows and one-per-row at three hundred passes
  every functional test either way.
- **Read `docs/WORKFLOW.md` before your first commit in a session** — branches (agents commit
  and push straight to `dev`), the label set, what to write on the issue, and the rules for
  working a tree that **other agents are editing at the same time**: stage explicit paths,
  never `git add -A`, and push your own commit by SHA so you don't publish someone else's.
  It also holds the rule for **breaking database changes**: existing self-hosted releases
  migrate themselves unattended on upgrade, so destructive schema changes go out over two
  releases (expand/contract) and the upgrade path is written down before the migration is.
- **State a constraint as the constraint, not as a deployment shape that happens to imply it.**
  The cloud API was pinned to `replicas: 1` + `order: stop-first` to stop two tasks racing
  `alembic upgrade head`. The reasoning was sound and the conclusion was far too strong: one
  replica plus stop-first is *by definition* a window with no API at all, and the web app — which
  rolls `start-first` and therefore stays up — answered 500 on every request for the length of
  every redeploy, because its first server hook fetches `/meta/tenant` before anything renders.
  The web app was up precisely so that it could render an error. The actual requirement was "one
  migration at a time", which is a Postgres advisory lock (`app/core/migrations.py`) and says
  nothing about replica counts; with it, the loser waits and then no-ops against a schema already
  at head. Two smells generalise. A comment that estimates its own cost (*"costs a few seconds of
  API downtime"*) is worth measuring — this one omitted the migration and the lifespan reconcile
  that the healthcheck already budgeted 90s for. And a service kept alive **through** a dependency's
  planned outage needs an answer for what it serves during it; "it stays up" is not one.
- Keep this file updated when architecture decisions change.
- Never leave a hardcoded user-facing string or an unscoped query — treat both as build breaks.
- After each module: register it, add its panels, add its i18n keys, run `i18n:check` + tests.
- At a phase gate: run migrations, run tests, then summarize what changed and stop.

## 12. MCP / AI access

The platform exposes an **MCP server** (shipped — see `docs/MCP.md`) so AI clients (Claude
Desktop/Code, agents) can work with the instance's data. Design rules:

- **Transport:** Streamable HTTP at `/mcp` (stateless, JSON responses), mounted on the API
  app (`app/core/mcp/`), behind Traefik. The older SSE transport is deprecated — do not use
  it. `SCHAKL_MCP_ENABLED=false` removes the surface.
- **Auth: API keys** (#20), which already carry **per-key permission scopes** — the
  permissions-per-MCP-key model. The proxy forwards the caller's `Authorization`/`X-API-Key`
  plus the tenant hostname on every internal call; keys are tenant-scoped, revocable and
  optionally non-expiring. An **OAuth 2.1 resource-server** layer (RFC 9728) is the later
  addition for clients that require it — the MCP server never runs its own login either way.
- **Tool surface:** every `/api/v1` operation is a tool, generated from the API's own
  OpenAPI spec (FastMCP) and proxied **in-process** back to the REST API — so every call
  travels `require_context` (tenant + RLS + permissions) exactly like the HTTP request it
  is, and MCP can never cross tenants or exceed the key's scopes. `/auth`, `/setup` and
  `/instance` are excluded. **Never** pass the incoming MCP credential to a downstream
  *external* service (confused-deputy risk).
- **Modular refinement:** each module can still contribute curated tools via `mcp.py` (see
  §6) where a richer shape than a 1:1 endpoint mapping is worth it; only enabled modules'
  routes exist, so the generated surface already tracks per-tenant modules.
- **Read-first is a key-minting decision:** a cautious instance mints read-only-scoped keys;
  the deny-by-default route permissions answer every call either way.
- **Moving target:** MCP evolves fast — the SDK is pinned (`fastmcp>=2.12,<3`) and tracks
  the spec; don't hardcode protocol details or well-known paths beyond what the SDK needs.

## 13. Per-tenant custom fields (custom attributes)

Each tenant defines their own **typed custom attributes** on any entity type (company,
contact, website, hosting, project, …) and can mark them **required**. This is a **core,
cross-cutting capability**, not per-module code.

- **Definitions:** `custom_field_definitions(org_id, entity_type, key, label_i18n,
  data_type, required, options_json, config_json, position, active)`. Unique per
  `(org_id, entity_type, key)`. `key` is an immutable slug; `label_i18n` holds per-locale
  labels (`{nl, en}`, tenant data). Types (v1): text, long text, number, boolean, date,
  datetime, select, multi-select, email, url, phone. `config_json` = per-type rules (options,
  min/max, regex, default, help text).
- **Storage:** each **customizable** entity carries a `custom JSONB` column keyed by
  definition `key`, with a GIN index. Use JSONB, **not EAV** — simpler, indexable, no join
  fan-out. If a single field later needs heavy filtering/reporting, promote just that one to
  a generated column or an indexed values table; don't EAV the whole thing.
- **Opt-in per module:** an entity becomes customizable via `CustomizableMixin` (adds the
  `custom` column + registers its `entity_type`). The registry exposes the customizable
  entity types to the tenant-admin UI, so new attachable modules get custom fields for free.
- **Validation (dynamic):** on create/update, the custom-fields service loads the tenant's
  definitions for that `entity_type`, builds a validator (types + required + options),
  coerces and validates `custom`, and rejects via the standard error envelope (i18n message
  keys) on failure. `required` is enforced here on every write.
- **API:** entity responses include `custom`; a definitions endpoint returns the schema per
  `entity_type` so any client can render fields, labels, order, and validation.
- **UI:** one generic `CustomFieldsForm` renders from definitions (every module inherits it);
  a Settings → Custom fields admin screen CRUDs definitions per entity type.
- **MCP:** read tools include custom values with their labels, so AI answers reflect each
  tenant's own fields.
- **Phase:** framework in **P1**; each module opts its entities in as it's built.

## 14. Employee PTO / leave (module)

An HR-adjacent module (`leave`) for employee time off. **"Employees" = the org's `users` /
memberships** (distinct from `contacts`, who are client people). Multi-tenant + i18n rules
apply as everywhere.

- **Tenant-configurable leave types:** `leave_types(org_id, key, label_i18n, paid, accrues,
  default_allowance, unit, carry_over_rule, expiry_rule, config)` — e.g. vacation, sick,
  unpaid, special leave. **Don't hardcode any country's law**; keep the rules in config so a
  tenant can model, for example, Dutch statutory vs extra-statutory (*bovenwettelijk*) days
  and their differing carry-over/expiry. Sick leave is a separate type, not deducted from
  vacation balance.
- **How a type draws on the agenda is the type's own property** (#270). `leave_types.calendar_display`
  is `all_day` (a full-width chip, the default) or `timed` (a positioned hour block in the day/week
  grid), editable in Instellingen → Verlof. It is a *type-level* choice, not a per-request one:
  whether an absence reads as "away today" or "away between 08:30 and 17:00" is a property of the
  kind of leave. It is also the only way free time / vrije tijd can be drawn per hour at all —
  its generated days carry no `start_time`/`end_time`, so nothing on the request implies a window
  — which is why the seeded free-time type ships `timed`. The **API** turns the window into the instant
  pair the grid positions by (`TeamLeaveItem.starts_at`/`ends_at`, resolved from the request's own
  times or else the scheduled day, anchored in the org zone): a leave time is local wall clock and
  a calendar block is an instant, and that conversion stays server-side so a block still starts at
  08:30 on the two days a year the clocks move. **Single-day absences only** — one instant pair
  from Monday morning to Friday evening would also claim every night in between, so a multi-day
  span keeps its full-day chip and `days` stays the honest per-day answer. The **Google Calendar
  mirror** (`google/calendar/push.py`) follows the same flag: a `timed` type pushes a timed event
  (its scheduled window resolved for a whole-day request, in `_emit_leave` where the schedule
  lives — the mirror never reads leave internals), an `all_day` type an all-day event, so one
  absence never reads as an hour block in-app and an all-day banner in Google.
- **Work schedules, not a weekly total** (#46). A JSONB week: per weekday a working block and
  **any number of break windows** inside it. Breaks are *windows*, not durations — you cannot
  subtract "30 minutes" from `15:00–17:00`, there is no break in it. A day is
  `(end − start) − Σ overlap(window, break_i)`. `hours_per_week` is **derived** from the schedule
  and rewritten on every save, never entered — but it stays authoritative while a profile has no
  schedule, so a pre-#46 part-timer on 32 h is not silently regranted the default's 40.
- **The week lives on the employment contract.** A schedule change usually *is* a contract change,
  so `EmploymentContract.schedule` is the authority for a date and the effective week is resolved
  **per date**: contract covering that day → `leave_profiles.schedule` (legacy) → the org's
  `leave_settings.default_schedule` (`08:30–17:00` minus a `12:30–13:00` break → 8.0 h/day,
  40 h/week). That is what keeps last year's leave priced at last year's roster, and why
  `compute_hours` resolves day by day: a span can cross a contract boundary. `NULL` at any level
  means *inherit*, not *unfilled* — which is why the backfill deliberately skipped employees who
  follow the org default, and why saving a week through the profile endpoint pushes it onto every
  contract that has not ended (an ended period keeps the week it ran under). Loops that price many
  days for one employee build the `date → week` lookup once (`schedule_resolver`), never per day.
  A schedule is employment data: it lives on the person (Instellingen → Gebruikers), not buried in
  leave settings.
- **Free time / vrije tijd is the full-time-norm shortfall** (#65, renamed from "ADV / roostervrije
  tijd" in #282). A `leave_types` type flagged `accrues_schedule_gap` (seeded key `roostervrij`, and
  the flag's column name, both **kept internal** to contain the rename) accrues, per contract period,
  `(norm − contract_hours) × weeks`, where `norm = week_hours(default_schedule)` — the org's default
  week, today 40 h, configurable. A full-timer (contract = norm) accrues **zero**; a reduced contract
  a pot of free days, rounded to the nearest half day and placed on the calendar by the recurring
  machinery (#107). This **replaced the old `scheduled − contract` basis**, which silently turned a
  38-h-contract-on-a-40-h-schedule divergence into 2 h/week nobody asked for (surfaced verifying
  #264). Only the *basis* changed: the recurring-free-day generator, the calendar feed, the Google
  mirror, the FIFO/expiry ledger and the #264 recompute-on-contract-change all consume it unchanged.
  **Two numbers stay** — `EmploymentContract.contract_hours_per_week` (the entered legal number,
  drives vacation *and* free time) and the contract's own week (drives per-day pricing + the
  days-equivalent) — because a spendable free-time *balance* needs a nominal schedule to place the
  free days against.
- **But the shortfall is only the default, and the choice is per contract.**
  `EmploymentContract.free_time_hours_per_week` is `NULL` to derive `max(0, norm − contract)`, `0`
  to say the free time is already in the roster, or an agreed figure no formula expresses
  (`LeaveService._contract_free_time`). The derived rule alone is right for one arrangement and
  wrong for the other: a 36-h contract worked as a nominal 40-h week takes the shortfall as movable
  free days, while a 32-h part-timer already working four 8-hour days would be handed
  `(40 − 32) × 52 ≈ 52 free days` on top of a roster that already gives them Friday off. **Both
  arrangements are ordinary and an agency holds them at once**, so "deactivate the type" — the only
  escape #282 left — is not an answer; per-org config cannot express a per-person fact. The
  employment wizard makes the choice explicitly in its werkweek step, so neither arrangement is
  ever inferred from a schedule the admin happened to enter. Never hardcoded CAO law (§14): a
  tenant who wants none of it still deactivates the type.
- **A free-time pattern says how many days, or how often** (#107, extended). `days_per_year` on
  `leave_recurring_days` spreads that many days evenly across the year on the anchor's weekday and
  **slides past** a holiday or a non-working day to the next candidate week, so the count the pot
  bought actually lands; `NULL` keeps the original fixed `interval_weeks` cadence ("every Wednesday
  afternoon"). Two modes because two different things are known: sometimes the arrangement *is* a
  rhythm, sometimes only the day count is, and for most contracts no whole number of weeks fits
  (38 h earns 13 days, which is almost but not every four weeks). A spread pattern also stores the
  nearest equivalent cadence, so a rolled-back release still generates sensibly. `_occurrence_plan`
  decides *which dates to attempt*; every rule about whether a day may be taken (balance, holiday,
  overlap, spent occurrence) is shared by both modes.
- **`GET /leave/free-time` is what the balance cannot say.** Free days are placed as *approved*
  leave, so once the generator has laid them all down, entitled and approved are equal and the
  per-type balance reads "0 h over" — true, and no answer to "when is my next day off" or "does the
  pot still cover my calendar". The overview carries placed / taken / upcoming, the next date, the
  days themselves, and the **overhang**: the future generated days a reprorated pot (#264) no
  longer covers. Withdrawing them takes explicit ids the caller was shown and goes through the
  ordinary `cancel` path, so the past stays locked and the Google mirror is told. Reported, never
  cancelled as a side effect of a contract edit.
- **A holiday costs no leave hours** (#47). `leave_holidays(org_id, date, name_i18n, active,
  source, key)` is tenant data seeded from a generator — the Dutch holidays *derived from Easter*,
  so 2028 needs no code change — and never law written in Python: Goede Vrijdag is worked at many
  Dutch employers, so the tenant deactivates what they work and a re-import never resurrects it.
  A December ARQ cron tops up next year, per org, via `run_per_org`.
- **Requests + approval:** `leave_requests(org_id, user_id, leave_type_id, start_date, start_time,
  end_date, end_time, hours, hours_override, status[pending/approved/rejected/cancelled],
  decided_by_user_id, note, decided_at)`. Members request; managers approve. Tenant-scoped, with a
  declared permission on every route (§15), like every module.
- **The API — not the browser — is the authority on `hours`** (#48). `LeaveService.compute_hours`
  walks the span day by day: not a scheduled working day → 0; an active holiday → 0; otherwise the
  day's scheduled window intersected with the requested one, minus every break it overlaps. It
  rounds **once**, on the summed minutes. `start_time`/`end_time` are nullable `TIME` — leave is a
  local-calendar concept, and a `TIMESTAMPTZ` would drag DST into a balance calculation for no
  benefit. `hours` is not accepted from a client and is recomputed on every edit, so a request
  moved into Kerst week gets cheaper. A span worth zero hours is rejected, never stored.
  `POST /leave/requests/preview` returns `{hours, days, breakdown}` so the form shows the number
  that will be stored, and *why*. A manager may set `hours_override` for what a schedule cannot
  express (four hours agreed on a day they were not scheduled); it is recorded against their name.
  **Approved requests are never retroactively recalculated** — new holidays do not rewrite last
  year's balance.
- **Self-approval is tenant policy, off by default** (#110). While off, a holder of
  `leave.request.approve` is an ordinary owner on their *own* requests: deciding their own
  pending request is refused (`errors.leave_self_approval`), an approval-relevant edit of their
  own approved request bounces to pending for the *other* approvers, and their own past is
  locked like anyone else's. Both the decide path and the edit path enforce it — one without the
  other is trivially sidestepped. The org's **sole** approver may always self-manage (a
  one-person agency must not deadlock). `leave_settings.self_approval` (Instellingen → Verlof)
  restores the trusted-approver behaviour; a pending request never notifies its own requester.
- **Balances:** entitlement + carry-over − used − pending, per user / type / year. Show the
  employee their remaining balance. **Over-requests warn but submit** (#109): advance/borrowed
  leave is the manager's call, so the form warns, the request goes through, the balance reads
  negative, and the approver sees the shortfall again on the pending list. Overlapping requests
  and zero-hour spans stay hard errors. Entitlement pots are **seeded automatically** (#105,
  #108) and **re-derived on any contract change** (#264): a contract create/correct/terminate
  recomputes that user's `generated` pots for the current and every future year it touches, in the
  same transaction — so terminating an open-ended contract mid-year (an employee who leaves early)
  reprorates the balance down, a raise via terminate-old + add-new folds both periods in, and a
  year the contract no longer covers loses its pot. A `manual` pot (`upsert_entitlement` — a
  carry-over correction, an override a schedule can't express) is never touched by the recompute,
  and the past is frozen: a closed year is never re-priced by a later correction (an approved
  request is likewise never retroactively recalculated). The first balance read of an ungenerated
  current/next-year pot still seeds it, and a December cron rolls next year forward for the whole
  staff — the bulk "Genereer" stays for backfills.
- **One balance from several pots** (#265). `leave_types.balance_group` lets types present as a
  single **employee-facing** balance while staying separate rows: the Dutch `vacation_statutory`
  + `vacation_extra` pots keep their own `default_weeks` and differing `carry_over_months` (so the
  wettelijk / bovenwettelijk split — and its expiry — survives) but roll up into one
  "Vakantieverlof" figure on My Day, `/leave`, the request form and the dossier. A `NULL`
  `balance_group` is standalone (its own singleton group). The combined figure is computed live in
  a **FIFO-by-expiry pot ledger** (`LeaveService._ledger`) — no data migration, no per-request pot
  column. `carry_over_months` is now **actually enforced**: a pot accrued in year Y lapses
  `carry_over_months` after Y ends (statutory → 1 Jul of Y+1; extra → 1 Jan of Y+6; `NULL` =
  never), so unused hours carry into the next year and then expire instead of silently resetting.
  Consumption **favours the employee** — a request draws from the soonest-to-expire valid pot
  first, so short-lived statutory is spent before long-lived extra and nothing lapses that could
  have been used. `GET /leave/balance/groups` returns the combined figure per group + the per-pot
  breakdown (accrual year, remaining, `expires_on`, `expired`); the per-type `GET /leave/balance`
  stays (its `remaining` is now expiry-aware and sums to the group's by construction, so
  `preview`, `summary` and the recurring generator keep working). Free time (standalone, carry 0)
  gains the same expiry — unused free-time hours lapse at year-end. The combined display is only
  safe *because* expiry is real: hiding the split without it would quietly drop the legal distinction.
  The **team roster** reads the same combined figures via `GET /leave/balance/groups?all_users=true`
  (#282) — every member's groups in one batched call, each tagged with its `user_id` — so the
  manager's table and the employee's own page can never show a different Vakantieverlof number.
- **Unit:** track in **hours** (matches time tracking and part-time contracts); display as days
  using the employee's **average scheduled working day** — never `hours_per_week / 5`, which tells
  a three-day part-timer their working day is 4,8 hours long.
- **Ties to time tracking:** approved leave shows on the timesheet and is excluded from billable
  capacity — never entered/counted twice as a time entry. Its per-day hours come from the API's
  breakdown (2 h Thursday, 5 h Friday), not from spreading the total evenly over the range.
- **Ties to calendar & reporting:** an in-app team leave calendar; approved leave syncs to
  Google Calendar (P3); feeds capacity / availability / utilization reporting.
- **Phase:** P2.

## 15. Roles & permissions (RBAC)

Authorization is **tenant-defined roles carrying explicitly granted permissions** (issue #19).
It is a **core, cross-cutting capability**, like custom fields (§13) — not per-module code.

- **Tables:** `roles(org_id, key, name_i18n, description_i18n, is_system, position)`,
  `role_permissions(org_id, role_id, permission)`, `membership_roles(org_id, membership_id,
  role_id)`. All org-scoped and RLS-forced. A membership may hold several roles; its effective
  permissions are the **union**. Plus an org-scoped `role_audit_log`.
- **RLS ≠ RBAC.** RLS enforces *tenant isolation* (Golden Rule 1); permissions enforce
  *capability within* a tenant, in the app layer. Never express a permission in an RLS policy.
- **Registry, not free text.** Each module declares its `PermissionSpec`s on its
  `ModuleDescriptor`; core declares core's in `app/core/permissions/catalog.py`. Naming is
  `<module>.<resource>.<action>`. `role_permissions` only ever stores a catalog key.
- **Scopes.** A spec may carry `scopes=("own", "any")` where the distinction is real. A scoped
  permission is **only ever stored suffixed** (`time.entry.write:own`), so a check with no scope
  means *"holds this at some scope"* and `:any` satisfies `:own`. A naive `key in granted` would
  403 every member on every scoped endpoint. `own` means *the row is theirs* — for a task, the
  **assignee**.
- **Two layers.** The **route declares** the base key (`require_permission("time.entry.read")`),
  which is what makes deny-by-default enumerable; the **service refines** with `:own` / `:any`
  where the rule depends on the row. Neither alone is enough — a decorator cannot see the row,
  and a service check cannot be enumerated.
- **404 vs 403.** Where an endpoint must not reveal that another user's row exists, load with a
  scope-aware fetch that raises 404 (`_owned_or_404`). A generic `require_for(key, owner_id)`
  raising 403 leaks existence on every get/update/delete-by-id.
- **Resolved once per request** in `require_context`, on the same statement as the membership
  lookup, and cached on `RequestContext` (`ctx.can` / `ctx.require`). No Redis cache — see
  `docs/PERFORMANCE.md`.
- **A company-group assignment is complete isolation, and the horizon has exactly four ways to
  leak** (#285). The repository enforces it whenever a model carries `company_id` *and* the read
  rides `scoped_select()`; the failures are all one of these, so check all four when you add a
  surface. **(1) No anchor** — the company link is indirect: a website belongs to its *domain's*
  client, a contact to whatever `company_contacts` links it to. The column match then finds
  nothing and filters *nothing at all*; such a model declares
  `__company_horizon_clause__(scope)` and every repository path picks it up. **(2) A hand-built
  count** — `total` computed with its own `select(count())` shows "2" above a list of one; use
  `scoped_count_select()`, and for raw SQL splice a bound `IN`. **(3) A hand-built cross-client
  read** — a window fold, a report, a summary tile: take the predicate from
  `horizon_condition()`. **(4) An entity-addressed surface** — the activity trail and a file list
  take `(entity_type, entity_id)` from the caller, so holding the type's read permission is not
  the same as being able to see *that row*; ask `entity_visible(ctx, …)`, which loads the record
  through its own repository. Rows attached to **no** client stay visible either way (they are not
  company data), and org-wide configuration stays readable — each config surface already has its
  own admin-only manage permission, which is what keeps a member from editing it.
  `tests/test_company_groups.py` closes with a sweep over every parameterless `GET /api/v1`
  plus a control run as the owner, so "nothing leaked" cannot quietly mean "nothing matched".
- **A reference into another module's rows crosses at a seam, never at a bare table read**
  (`app/core/directory.py`). §6 forbids importing another module's internals, so every borrower
  grew its own `SELECT … WHERE org_id = :oid` — which is failure mode **(1)** one layer out: an
  interaction's participant chips and a note's @mentions resolve *contacts*, whose client lives
  in `company_contacts` and in no column the borrower is allowed to know about, so the read was
  tenant-correct and horizon-blind. `visible_ids` / `ids_by_email` answer through the target
  model's own repository (`horizon_condition()`), keyed by the `__entity_type__` registry core
  already holds, so the rule stays where it was declared and there is exactly one copy of it —
  including the **stricter client rule**, which a model states as `__portal_horizon_clause__`
  and the seam prefers for an `is_portal` caller (restricted staff still see an unattached
  contact; a client never does). Reach for the seam whenever a module must *name* rows it does
  not own; teaching the borrower the join is the mistake this exists to prevent.
  **`entity_visible` prefers the same clause** (#266). It is the *other* seam onto the same
  question, and it was answering with the staff rule — so `GET /files`, which takes
  `(entity_type, entity_id)` from the caller and declares `no_permission_required` ("any
  signed-in member", a portal login included), let a client enumerate the documents attached
  to a **draft invoice** the service otherwise 404s them off. The activity trail never was
  exposed the same way — its router returns `[]` for any portal caller first — and that is the
  point: one of the two callers remembered and one did not, which is what a shared predicate is
  for. `PORTAL_CLAUSE_ATTR` lives in `core/scope.py` because `directory.py` imports it.
- **Deny-by-default.** An `/api/v1` route with neither `require_permission(...)` nor an explicit
  `no_permission_required("reason")` is a build break. Two tests enforce it: an introspection
  lint and a behavioural sweep that calls every operation as a member holding nothing.
- **System roles.** `owner` / `admin` / `member` / `client` are seeded per org. `owner` holds
  exactly `["*"]`, immutable and undeletable — that is what keeps a mistake made anywhere else
  fixable. The other three are undeletable and key-immutable but freely permission-editable and
  duplicable. `admin` holds an explicit full list, never a wildcard, so a tenant can restrict it.
- **Never lock the tenant out.** Every mutation that could remove the last membership holding
  `*` or `settings.roles.manage` is applied, flushed, re-counted, and rolled back with
  `409 errors.last_role_manager`.
- **The frontend guard is UX, not security.** `can()` in the web mirrors the API's
  `PermissionSet.has` exactly and decides what to *render*. The API is the boundary. The
  **client portal** (#193) is the hardest case: it renders the *same* components as staff, and
  detail pages compose panels and shared rows without a portal filter — so every write control on
  a client-reachable surface must self-gate on its API permission, including "use-mode" ones (a
  checklist tick, a complete-toggle, a drag handle, an inline "＋ nieuw"). `!isPortal` is not the
  gate; the API's own key is (`docs/UX.md`, the client-portal entry). The client's whole write
  surface is its own task comments, dashboard/nav layout and notification inbox — nothing else.
  A screen where *every* control writes is gated whole — the route load, and the tab or card that
  links to it — and its **read** is gated too: the org-wide task-template and checklist
  repositories sat behind `tasks.task.read`, which a client holds, so the portal reached the
  agency's internal process library and its create form. They now read on `tasks.template.apply`
  and `tasks.task.write`.
- **"External login" is one fact, and it is the `client` role** (#274). `ctx.is_portal` is true for
  a contact-linked portal membership (#193) *and* for any membership holding the seeded `client`
  role — the definition #252 already adopted when it floored that role's company horizon to the
  empty set. Every "what a client may see" narrowing hangs off it, so gating one on the contact
  link alone silently exempts a directly-invited client: `contacts` carries no `company_id`, its
  narrowing was portal-only, and such a login read the agency's whole address book. Resolved on the
  membership statement (a `bool_or` beside the permission aggregate), never as a second query.
- **A permission and a horizon fail identically, so the horizon must speak for itself.** Both gates
  refuse; §15's 404 rule means the client sees `errors.not_found` either way, and the admin's only
  lever — grant more permissions — cannot fix an empty horizon. So a client-role login scoped to no
  company is told which piece is missing (`errors.no_company_scope`, 403 — it describes their own
  account, not our rows, and leaks nothing), and Instellingen → Gebruikers badges the account
  (`MemberRead.company_scope_empty`). Writes that would land outside a client's horizon are refused
  *before* the row is written, never after.
- **Signing in as someone else is one mechanism with two kinds** (`docs/IMPERSONATION.md`). The
  instance owner's cross-tenant impersonation (#26) and an agency staff member signing in as a
  client's contact person (#296) share the grant: a short-lived JWT in its own cookie *beside* the
  real session, with permissions resolving for the **target**, so an impersonated session is never
  more powerful than the account it entered. The tenant-level kind carries what an untrusted-by-
  default caller needs: its own permission (`portal.login.impersonate`, never implied by
  managing the login), a target that can only be a subject-linked portal login, and — since #266
  — a session **capped to the intersection** of target and impersonator: permissions
  (`PermissionSet.narrowed_to`) *and* company horizon alike. A subset cannot escalate, so the
  invariant holds by construction rather than by a gate, and a scope degrades (`:any` against a
  caller's `:own` keeps `:own`) rather than dropping a screen they can open anyway. Only
  `is_superuser` still refuses outright (`errors.impersonation_escalation`): a different axis
  (§5), not a permission, so no intersection bounds it. The cap is `portal`-only — an instance
  owner holds no membership in the tenant, so capping would leave them nothing.
  It replaced a `covers` **refusal**, and the reason generalises: the refusal stated the
  invariant indirectly and **coupled two things that should not be coupled** — every grant to the
  tenant-editable `client` role shrank the set of staff who could impersonate at all, and #266's
  invoice read locked out every `member` overnight with *"impersonation stopped working and we
  changed nothing"*. Prefer narrowing a session to refusing one wherever the security property
  survives it. The horizon half was never guarded at all: `covers` compared permissions, so a
  member scoped to one company group could read a second client through a client's session.
  **A capped session must say so** (`/meta/me`'s `impersonation_narrowed`, on the banner): the
  point of the feature is seeing what the client sees, and an unlabelled partial view is a
  screen that lies about someone else's account.
  **Stopping declares no permission on purpose**: it runs as the impersonated account, and gating
  the way out behind a permission that account cannot hold would trap someone inside the session.
- **Scope is what lets one key serve a client and the agency at once** (#266, `docs/INVOICING.md`).
  Before you grant an existing permission to `client`, list every route that declares it: reads
  cluster, and `invoicing.invoice.read` gated seven endpoints of which only three were documents —
  the rest were the seller's bank details, the price list, the template library and the org-wide
  unbilled backlog with every employee's hourly rate on it. None of those is a row a company
  horizon could narrow (there is no client whose price list this is), so the **scope** is the only
  thing that can fence them: `:any` on the module's own surfaces, `:own` for the documents, and the
  horizon still decides *whose*. **Externality is a separate axis from breadth** — "a client never
  sees a draft" follows `ctx.is_portal` (#274), not the scope, because restricted staff must keep
  seeing the drafts they write; it belongs on the model as `__portal_horizon_clause__` and reaches
  every path through the service's portal repository, never per read method (#285). And the web
  mirrors the **key and the scope**, never `!isPortal`, on every control *and* on the nav item
  (`NavItem.requiresScope`).
- **A module that ships later** brings its own permissions; a startup reconciler grants them to
  each org's system roles exactly once, tracked in `org_settings.applied_permission_defaults`.
  A migration must never import the catalog (`docs/WORKFLOW.md`). **Widening an existing key's
  defaults is invisible to that diff** — it is keyed on `spec.key`, which is already applied, and
  no per-role diff can tell *never offered* from *offered and unticked*. So a widening is a
  `DefaultsRevision` in `reconcile.py`: an append-only entry with its own `@rev:` marker in the
  same array, granting the new default and — where a key became scoped — rewriting the bare stored
  string to `:any` everywhere it lives, roles and API-key scopes alike. That rewrite grants nothing
  (`PermissionSet.has` already read a bare key as the broadest); it is what stops
  `validate_permissions`, which refuses to *store* a scoped permission bare, from 422-ing the
  tenant's next save of a role that was working fine.

## 16. Activity trail / audit log (core capability)

Every record that can be changed carries a visible paper trail of *what changed, by whom, when*.
This is a **core, cross-cutting capability** (issue #67), like custom fields (§13) and permissions
(§15) — not per-module code, and not the notification log (`NotificationEvent` is about
*delivery*; its vocabulary is the notifiable subset, and it records no field edits).

- **One table.** `activity_log(org_id, entity_type, entity_id, actor_user_id, actor_name, action,
  payload)` in `app/core/activity/`, org-scoped and RLS-forced like every domain table. `entity_id`
  carries **no FK** — the trail outlives the record it describes. `TaskActivity` predates this and
  still stands; folding it in is a later step.
- **Opt in like custom fields.** An entity adds `AuditableMixin` and sets `__entity_type__` (the same
  attribute `CustomizableMixin` reads); that registers it as auditable. A core-contributed panel then
  renders the trail on its detail page — the company hub via an API `PanelSpec`, a project/contact via
  a typed `EntityPanelSpec`, both reading `GET /api/v1/activity`.
- **A write made while impersonating names the impersonator** (#296). An impersonated request runs
  as the target — its permissions, its horizon, its writes — so a trail carrying only the actor
  would say the *client* did it, and the single fact worth auditing (someone acted through that
  account) is the one missing. `activity_log` and the tasks module's own `task_activities` both
  carry `impersonator_user_id` + `impersonator_name`, written by whichever service records the
  change and snapshotted for the same reason the actor is.
- **The actor is snapshotted, never joined live** (§14's #64 rule, generalised). `actor_name` is
  written at record time; the live account wins while it exists, a departed one reads
  "Naam (verwijderd)", and a genuinely absent actor is the system. An audit trail whose actor
  evaporates is not an audit trail.
- **A service records; a write is not a permission.** `ActivityService.record` runs in the writing
  request's transaction, so the change and its trail entry commit atomically. Reading the trail is
  gated on `activity.read`; *recording* is a side effect of a write the caller was already allowed to
  make, never its own grant. `payload` for an edit is `{changes: {field: {from, to}}}` — the record's
  own definition fields, not its freeform notes or custom JSONB.

## 17. Spreadsheet import & export (core capability)

Every entity a tenant can list, they can take out and bring back in — CSV, TSV, Excel or a block
pasted from a spreadsheet. Core owns the mechanics (`app/core/impex/`); a module only **describes
its shape**, exactly as it does for custom fields (§13) and panels (§6).

- **A module opts an entity in with an `ImpexDescriptor`** in its `impex.py`, declared on its
  `ModuleDescriptor`. It names the columns, the permissions, the module's own list service
  (`fetch_page`) and its own write path (`create_row`/`update_row`) — an imported row fires the
  same validation, events and side effects a form submit would. Import is not a backdoor around
  the service layer. `importable=False` is export-only (approval-bearing records like leave must
  be requested, never bulk-written).
- **Headers are stable keys, never labels.** An export re-imports into the same org unchanged.
  Labels are a display concern: `label_key` for built-ins, the tenant's own `label_i18n` for
  custom fields — which the **client** resolves, because the API does not pick a locale for
  someone else's content.
- **Without a `mapping`, the header *is* the mapping** and must be exact keys; an unknown one is
  fatal. **With one** (`{file column index: key}`), an unmapped column is skipped instead. Both
  contracts are deliberate: the first is what makes a round-trip and an automated caller stable,
  the second is what makes an arbitrary spreadsheet importable. `aliases` only ever feed
  `/inspect`'s suggestions — they never widen the header-key contract.
- **The mapping is positional, so it is fingerprinted.** `/inspect` returns a digest of the bytes
  and `/import` refuses a mismatch (409): applying a mapping to a *different* file writes the
  wrong columns into the right fields, with every row valid and every value wrong.
- **A check the row report cannot name is a check the preview does not have** (#289).
  Validation that lives only in the service runs *after* the report is built, so its failure
  returns as a request-level 422 naming no row — and the user hunts through blank cells for one
  number a digit short. So a validated shape gets a column `data_type` in the engine: `phone`
  coerces to E.164 against the row's own country (`region_field`, resolved exactly as the owning
  service resolves it) and reports `errors.invalid_phone` against that row and that column. Being
  a *pre*-check it may never reject what the write would accept, which is why an unchanged value
  on an existing row is grandfathered here too (§3, issue #256) — and why a *contributed* column,
  whose target row only exists at write time, is validated as a create.
- **A module contributes columns to another module's entity with an `ImpexExtension`** — the
  panels pattern, applied to import/export, so the company import can carry the client's contact
  person without companies importing contacts' internals. Keys are namespaced by the contributor,
  a contributed column may never be `required`, and both are asserted at **mount** time. `apply`
  runs in the import's own transaction through the contributing module's service, and **never on
  a dry run** — so anything that could fail inside it must be expressible on the columns
  themselves. `hydrate` batch-loads what its export getters read, or the export goes N+1.
- **Both the column catalog and the export header are caller-dependent**: contributed columns are
  filtered on the contributor's own permissions, so a caller who cannot write contacts never sees
  them rather than hitting a mid-import 403 that rolls the whole file back.
- **Reading the bytes is `parsing.py`, and it is defensive**: the format comes from the content
  (a zip magic number), not the filename; every cap is checked *before* the work it bounds (the
  byte cap before decoding, the zip's declared sizes before decompressing, the column cap while
  reading); and over any limit is an error, never a truncation — silently importing the first
  2000 rows of a 2500-row list is the worst outcome available, because it looks like it worked.
- **A reference is whatever the resolver says it is.** `fk_resolvers` batch-resolve a column's
  raw cells once per file, and the contract is one line: **a `str` return is an error key, and
  anything else is the resolved value.** That is what lets `party` exist without core learning
  its shape — the cell is a token (`agency`, `company`, `company:Acme`, `employee:jan@bureau.nl`,
  `contact:info@klant.nl`, `app/core/impex/party.py`) and what lands in the values dict is a
  `PartyRef` the owning service validates exactly as it would one from the form. An unprefixed
  cell is *refused*, never guessed: one e-mail address is an equally plausible colleague and
  client contact, and picking one writes the wrong kind of party with every row valid.
  `provider_resolver(kind)` exists for the same reason in miniature — a tenant with a
  "Cloudflare" registrar *and* a "Cloudflare" DNS row makes the generic name resolver useless.
- **`clearable` governs references too, and a reference may be the upsert key.** Whether an
  emptied cell detaches is a property of the link, not of it being a link: hosting with no
  client is shared infrastructure (a real state the file must express), a domain with no client
  is nonsense. And a website has no name of its own, so `natural_keys=("domain",)` matches on
  the raw cells `find_existing` is handed — matching and resolution are independent lookups and
  neither waits on the other. Without it, re-importing an export hits the unique index on every
  row: the worst answer available to "I edited two cells and imported it again".
- **A pre-check must normalise the way the write does.** `find_existing` matches domains on the
  *normalised* name because `DomainCreate` normalises too — matching the raw text finds nothing,
  decides the row is a create, and then 409s on a name that was already there. Same failure
  shape as #289's, one layer up: the check the report can name has to model the write.
- **Bulk is its own capability, and it is not an employee's by default.** `impex.export` /
  `impex.import` are staff-only and sit *on top of* each entity's own read/write permission: a
  client-portal login holds `companies.company.read` for its own company and must never be able
  to download the client list. They default to **admin only** (owner call): taking the client
  list or the domain register out of the building in one file is a different act from opening a
  record, and one an agency decides per person. The pair is one capability across every entity,
  so that decision is made once rather than per screen — granting `impex.export` opens exactly
  the entities the role can already read.
- **One entry point, everywhere.** Every list that can travel by spreadsheet renders the same
  `ImpexBar` (`$lib/core/impex/ImpexBar.svelte`) beside its column picker — Export carrying the
  list's current filters, Import opening the shared wizard — and every download goes through the
  one proxy at `/impex/[entity]/export`. Both gates are mirrored client-side (bulk *and* the
  entity's own), so a control that would 403 is never drawn. Instellingen → Import & export is
  the overview of what can travel at all and exports the whole unfiltered set; it is not where a
  user with a spreadsheet of domains should have to look.
- Large imports as a background job are still deferred (issue #77); `MAX_IMPORT_ROWS` is what
  keeps the synchronous path honest until that lands.

## 18. Bulk edit & delete (core capability)

A selection is a spreadsheet you never had to leave the app to make, so it uses the same
description of the same entity. `app/core/bulk/` owns the mechanics; a module declares a
`BulkDescriptor` that **borrows its `ImpexDescriptor`** — the column vocabulary, the batched
reference resolvers, and `update_row`, which is its own service call. A second write path is the
one way a bulk edit could stop meaning what an edit means: fifty picked rows must get the
validation, activity line, events and custom-field rules that fifty visits to the form would.

- **`editable` is an allow-list, never a deny-list.** What may be set across a selection is a
  product judgement no column can carry — a domain's `name` is importable and must never be
  bulk-writable — so a column added to an import tomorrow is not silently bulk-writable today.
  `check_descriptor` fails at **import time** on a key that names no column, names a derived one,
  or names a per-row type (`phone`, `email`: a national number is read in *that row's* country,
  so one shared value is meaningless).
- **A bad shared value is the caller's; a bad row is the row's.** An unknown status or an
  unresolvable client is resolved once, before anything is touched, and is a **422 for the whole
  call** — every row would fail on it identically. Row-level trouble is *reported, never raised*:
  raising mid-batch rolls the request back and undoes the forty-nine that worked. Which is why
  **every row runs in its own SAVEPOINT** (`begin_nested`) — catching an error without one leaves
  the session poisoned for everything after it — and why only `AppError` is caught: that is the
  vocabulary a service speaks when it *decides* to refuse, and anything else surfacing as "3 rows
  skipped" is a bug nobody will ever find.
- **Absent means leave alone; explicit `null` means clear** (`InteractionBulkLinks`' rule,
  generalised). The dialog opens blank over rows that disagree with each other, so "I did not
  fill this in" can never mean "empty it on all of them". `required` overrules `clearable` for
  the same reason it does on an import.
- **Routes are generated per entity, never generic** (§15): each declares that entity's own
  write/delete permission, so deny-by-default stays enumerable. **No new capability gates it** —
  the two precedents disagree deliberately and both say why: impex earns `impex.export` because
  taking the client list out of the building in one file is a *different act*, while bulk review
  carries the plain review permission because approving forty emails you may each approve is *the
  same act, repeated*. A bulk edit is the second kind. Unlike impex it **does** carry its
  module's `license_write_gate`: a bulk write must not be the one way an uncovered module can
  still be written to.
- **An entity with no import shape still gets a bulk delete.** `impex` is optional; a descriptor
  that names its `entity`, its model, its delete permission and its service call is complete.
  Deleting needs no column vocabulary, and requiring one would have excluded the two entities
  where a batch is most obviously wanted: a run of **draft invoices** and a run of mis-logged
  **contact moments**. Neither has a field a selection could share, so neither mounts an update
  route — and the web's `BulkUpdateEntity` / `BulkDeleteEntity` are separate types read off the
  generated client, so asking for the wrong one is a compile error.
- The web mirrors it in `$lib/core/bulk/`: `BulkToggle` (the ✎, **last** in every toolbar, which
  switches the checkboxes on) and `BulkBar` (the actions, in their own strip above the table) —
  a list has no selection gutter until someone asks for one, and the actions are not more
  toolbar (`docs/UX.md`). Both take the same `BulkConfig`, so a page configures once and spreads
  into both. Plus one dialog, one outcome banner, and `bulkUpdateAction(event, entity)` spread
  into each list's actions the way `impexAction` already is. Field definitions live in web code
  beside `columns.ts`, because the picker options are lookups the page already loaded and no
  generic endpoint could hand them back without shipping the tenant.
