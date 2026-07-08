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

Internal codename: `PLATFORM` (replace with your own). The **brand shown to users is
per-tenant** and never hardcoded.

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
| Typed client  | `openapi-typescript` client generated from the API's OpenAPI spec |
| Database      | PostgreSQL (with Row-Level Security) |
| Jobs & cache  | Redis + ARQ |
| Auth          | App-native at the API: FastAPI Users (local username/password, verification, reset) + Authlib (OIDC relying-party, optional & toggleable) · Google OAuth for Workspace scopes |
| Infra         | Docker Compose · Traefik · deployed on Hetzner · Cloudflare Zero Trust |
| MCP / AI       | MCP server over Streamable HTTP (OAuth 2.1 resource server) via the official Python MCP SDK / FastMCP; mounted on the API app; tools contributed per module, read-first |

Ship these as separate containers in one Compose file: `web`, `api`, `worker`, `db`, `redis`, `traefik`.

## 4. Repository layout (monorepo)

```
apps/
  api/app/
    core/          # config, db session, tenancy, auth, i18n, module registry, RLS helpers
    modules/
      companies/   # models.py schemas.py service.py router.py panels.py migrations/
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
its own instance, seeded with **one org** at install — so day-to-day it runs as a single
tenant, and the agency's clients are `companies` (data), not tenants. Keep `org_id` + RLS
on every table anyway: it's near-free now and is the only thing that lets the *same code*
run a future multi-org **cloud** version with the tenant resolved by hostname. Don't take
shortcuts that assume one org.

## 6. Module pattern (how to add a domain)

An **API module** is a package under `apps/api/app/modules/<name>/` exposing:
- `models.py` — SQLAlchemy models, all with `org_id`, all inheriting the shared `Base`.
- `schemas.py` — Pydantic request/response models.
- `service.py` — business logic (no DB access outside the tenant-scoped repository).
- `router.py` — REST endpoints under `/api/v1/<name>`, mounted by `main.py`.
- `panels.py` — optional: declares what this module attaches to a **company** (title +
  data provider) so the company detail view can compose it. This is the modular hub.
- `mcp.py` — optional: the MCP tools/resources this module contributes (e.g.
  `companies.find`, `companies.recent_projects`), registered onto the MCP surface alongside
  the router. Read-only by default; each tool goes through the tenant-scoped service layer.
- entities that should accept **tenant-defined custom attributes** use the shared
  `CustomizableMixin` (adds a `custom` JSONB column and registers the `entity_type` with the
  custom-fields core — see §13).
- `migrations/` — Alembic revisions owned by the module.
- registers itself into the **module registry** (name, router, models, panels, mcp tools,
  cron jobs, i18n namespace).
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
custom_domain, default_locale, enabled_modules[])`.
The web app loads the tenant theme on first render and applies it via CSS custom properties.
Emails and generated PDFs use the same tenant branding. Tenant resolution: `custom_domain`
or `<slug>.PLATFORM_BASE_DOMAIN` → org.

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
- **Formatting:** dates, numbers, currency via `Intl`, default timezone `Europe/Amsterdam`,
  currency `EUR`.

## 9. Conventions

- REST: plural nouns, `/api/v1/<module>/<resource>`; cursor or page/limit pagination;
  consistent error envelope `{ error: { code, message, fields? } }` (message is an i18n key).
- Auth: a single FastAPI dependency yields `(current_user, current_org, role)`.
- Tests: `pytest` (API, incl. a tenant-isolation test per module) + Playwright (web smoke).
- Migrations: one per change, named `<module>_<verb>_<noun>`.
- Commits: small, scoped, conventional (`feat(time): add weekly timesheet grid`).
- **Definition of done** for a feature: migration written, endpoints + tenant scoping,
  web UI, `nl.json` + `en.json` keys, test for tenant isolation, docs/OpenAPI updated.

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

## 11. Working agreement (for Claude Code)

- Start each phase in **plan mode**; propose the plan and wait for approval before coding.
- **Read `docs/UX.md` before building or changing any screen** — it records the product's
  design language (mobile-first, use-vs-edit modes, European dates, template patterns,
  where admin config lives) and the UX mistakes already corrected once.
- **Performance and lean code are first-class requirements.** Slow-feeling pages are bugs.
  Keep SSR loads minimal (shared lookups in layout loads, `meta=false`/`count=false` on
  pickers, no redundant API calls or queries), prefer fixing the data path over adding
  libraries, and when a page feels slow, count its API calls/queries before writing code.
  **Read `docs/PERFORMANCE.md`** — the data-path rules and the per-screen checklist.
- Keep this file updated when architecture decisions change.
- Never leave a hardcoded user-facing string or an unscoped query — treat both as build breaks.
- After each module: register it, add its panels, add its i18n keys, run `i18n:check` + tests.
- At a phase gate: run migrations, run tests, then summarize what changed and stop.

## 12. MCP / AI access

The platform exposes an **MCP server** so AI clients (Claude Desktop/Code, agents) can
answer questions about clients, their recent projects, time, etc. Design rules:

- **Transport:** Streamable HTTP at `/mcp`, mounted on the API app, behind Traefik. The
  older SSE transport is deprecated — do not use it.
- **Auth:** the MCP server is an **OAuth 2.1 resource server**. It validates access tokens
  issued by the platform's own auth (or the enforced external OIDC provider) and implements
  RFC 9728 Protected Resource Metadata (`/.well-known/oauth-protected-resource`). It does
  **not** run its own login. Use the official Python MCP SDK / FastMCP for the OAuth plumbing.
- **Tenant + permission scoping:** every tool resolves `current_user` + `current_org` from
  the token and calls the **same tenant-scoped services/repositories** as the REST API, so
  MCP can never cross tenants or exceed the user's role. **Never** pass the incoming MCP
  token to a downstream service (confused-deputy risk) — tools call internal services directly.
- **Modular:** each module contributes its tools via `mcp.py` (see §6); only enabled modules
  expose tools. Scopes: `mcp:read` by default, write scopes added later per action.
- **Read-first:** ship read tools only at first (`companies.find`,
  `companies.recent_projects`, `time.summary`, …). Add writes later behind explicit scopes
  and step-up authorization.
- **Moving target:** MCP evolves fast — pin the SDK and let it track the spec; don't hardcode
  protocol details or well-known paths beyond what the SDK needs.

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
- **Requests + approval:** `leave_requests(org_id, user_id, leave_type_id, start, end,
  amount, status[pending/approved/rejected/cancelled], approver_id, note, decided_at)`.
  Members request; admins/managers approve. Tenant-scoped + role checks like every module.
- **Balances:** entitlement + carry-over − used − pending, per user / type / year. Show the
  employee their remaining balance; block/warn on over-request.
- **Unit:** track in **hours** (matches time tracking and part-time contracts); display as
  days using the employee's contract hours.
- **Ties to time tracking:** approved leave shows on the timesheet and is excluded from
  billable capacity — never entered/counted twice as a time entry.
- **Ties to calendar & reporting:** an in-app team leave calendar; approved leave syncs to
  Google Calendar (P3); feeds capacity / availability / utilization reporting.
- **Phase:** P2.
