# vlotr

A **multi-tenant, modular, white-label agency operations platform**. One codebase runs many
agencies (tenants); each tenant manages **companies** (their clients) and attaches people,
websites, hosting, projects, retainers, deals and time to them. SSR web app, installable as a
PWA. Primary language **Dutch** (`nl`, default UI), fully internationalized (`en` = source).

> `CLAUDE.md` is the project constitution and source of truth. Read it before contributing.
> Internal codename: **vlotr**. The brand shown to users is **per-tenant** and never hardcoded.

## Stack

| Layer        | Choice |
|--------------|--------|
| Web          | SvelteKit (SSR) + `@vite-pwa/sveltekit` · Tailwind · Bits UI · Paraglide (i18n) |
| API          | FastAPI · Pydantic v2 · SQLAlchemy 2.0 (async) · Alembic → OpenAPI |
| Typed client | `openapi-typescript` generated from the API's OpenAPI spec |
| Database     | PostgreSQL (Row-Level Security) |
| Jobs & cache | Redis + ARQ |
| Auth         | FastAPI Users (local) + Authlib (optional OIDC) |
| Infra        | Compose · Traefik · images on **GHCR** (`ghcr.io/vlotr-crm/vlotr-*`) |

## Layout

```
apps/api/     FastAPI backend (core + self-registering modules)
apps/web/     SvelteKit PWA
messages/     en.json (SOURCE) + nl.json (required, complete) — flat namespaced keys
infra/        compose.yaml, traefik, seed
scripts/      i18n-check, gen-client
```

## Quick start (local, via Compose / podman)

```bash
cp infra/.env.example infra/.env      # then edit secrets
docker compose -f infra/compose.yaml up --build
```

- Web: http://app.localhost   ·   API + docs: http://api.localhost/docs   ·   Traefik: http://localhost:8080

**Rootless podman** (can't bind port 80): start the socket once, then use a high port —
```bash
systemctl --user start podman.socket
TRAEFIK_HTTP_PORT=8080 TRAEFIK_DASHBOARD_PORT=8081 \
  podman compose -f infra/compose.yaml up --build
```
then browse **http://app.localhost:8080** (Chrome resolves `*.localhost` to 127.0.0.1 automatically).
Default admin: `admin@example.com` / `changeme123`.

### API development (host)

```bash
cd apps/api
uv sync
uv run alembic upgrade head
uv run python -m app.seed          # one org + owner + settings
uv run uvicorn app.main:app --reload
uv run pytest
```

### Web development (host)

```bash
cd apps/web
pnpm install
pnpm run machine:messages          # compile Paraglide from ../../messages
pnpm dev
```

### Cross-cutting scripts (repo root)

```bash
pnpm run i18n:check     # fails on key drift between locales (nl must be complete)
pnpm run gen:client     # regenerate the typed API client from the API's OpenAPI
```

## Docs

`docs/` holds the topical guides: [`DEPLOY.md`](docs/DEPLOY.md) (self-host, upgrades, env),
[`SSO.md`](docs/SSO.md) (OIDC login & the redirect URI), [`GOOGLE.md`](docs/GOOGLE.md) (Workspace
integration design), [`WORKFLOW.md`](docs/WORKFLOW.md) (branches, commits, breaking DB changes),
[`PERFORMANCE.md`](docs/PERFORMANCE.md), and [`UX.md`](docs/UX.md).

## Phases

P0 Foundation (this) → P1 MVP (contacts/tasks/time + custom-fields) → P2 Agency core
(projects/pipeline/leave/reporting) → P3 Google Workspace → P4 Automation + MCP server.
Build one phase at a time; stop at each gate. See `CLAUDE.md` §10.
