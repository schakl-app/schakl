# Kickoff prompt — paste this as your first message to Claude Code

*(Put `CLAUDE.md` at the repo root first so Claude Code reads it automatically.)*

---

You're building a greenfield **multi-tenant, modular, white-label agency operations
platform**. `CLAUDE.md` at the repo root is the source of truth for architecture,
conventions, and the phased plan — read it fully. The **Golden Rules** in it are
non-negotiable; if anything I ask conflicts with them, stop and tell me.

Do not write any code yet. First:

1. Restate each Golden Rule in one line so I know you've absorbed CLAUDE.md.
2. Produce a **plan for Phase 0 (Foundation) only**:
   - Monorepo scaffold and Docker Compose (`web`, `api`, `worker`, `db`, `redis`, `traefik`).
   - FastAPI core: tenancy layer (`org_id` scoping + Postgres RLS), **auth** (FastAPI Users
     for built-in username/password + Authlib for optional OIDC federation that disables
     local login when enforced), i18n catalog loading, and the module registry (with a seam
     for module-contributed **MCP tools** — the MCP server itself lands in Phase 4).
   - SvelteKit + PWA shell with **runtime per-tenant theming** and **Paraglide** i18n
     (`en` as source locale, `nl` required and set as the default display language).
   - The `companies` module implemented **end-to-end** as the reference for the module
     pattern — including a company detail page that composes module "panels", and a `custom`
     JSONB column via `CustomizableMixin` to seed the per-tenant custom-fields capability —
     proving that websites/hosting/contacts can later attach as their own modules and that
     tenants can add typed, required custom attributes to any entity.
   - `scripts/i18n:check` (fails on key drift) and the OpenAPI → TypeScript client generator.
   - Basic CI.
3. Wait for my approval of the plan before implementing.

Honor these from the very first commit, not later — they are painful to retrofit:

- `org_id` + RLS on every domain table; all data access tenant-scoped. Each install is
  seeded with one org (self-hosted, single-tenant) but the code stays multi-tenant so a
  cloud version needs no rewrite — no shortcuts that assume one org.
- Every user-facing string in `messages/en.json` (source) + `messages/nl.json` (required,
  default UI language). No hardcoded text anywhere; `nl` is never partial.
- Built-in username/password works out of the box with no external service; OIDC is optional
  and, when enforced, disables local login.
- Per-tenant branding (name, logo, colors, domain, default locale) resolved at runtime.
- Each domain is a self-registering module; the company page composes panels from enabled
  modules.
- All schema changes via Alembic migrations.

Stack is locked (see CLAUDE.md): SvelteKit + PWA / Tailwind / Paraglide · FastAPI /
SQLAlchemy 2.0 / Alembic / Pydantic v2 · PostgreSQL · Redis + ARQ · auth via FastAPI Users
(local) + Authlib (optional OIDC) · Google OAuth · Traefik / Docker Compose.

Start with steps 1 and 2. Show me the plan.

---

## How to run the build after the plan is approved

- Keep working **one phase at a time**. At each phase gate, have Claude Code run migrations
  and tests, summarize, and stop for your review before moving on.
- Later phases add their own modules per CLAUDE.md's plan — e.g. Phase 2 includes an
  **employee PTO / leave** module (`leave`, see §14): tenant-configurable leave types,
  request/approval, balances tracked in hours, tied into the timesheet and capacity reporting.
- When you add a new attachable type later (e.g. `domains`, `ssl`, `mailboxes`), just say:
  *"Add a new module `<name>` following the module pattern in CLAUDE.md, with a company
  panel and nl/en keys."* — nothing else should need to change.
- For translations: *"Translate `messages/nl.json` into `messages/<locale>.json`, keep keys
  identical, then run `i18n:check`."* Because the catalogs are flat and namespaced, this is
  a clean one-pass job for me or a translator.
