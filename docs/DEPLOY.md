# Deploying vlotr

Self-hosted, single-org install (CLAUDE.md §5). Two Compose files, both self-contained:

| File | Ingress | Use when |
|---|---|---|
| `infra/compose.yaml` | Traefik, publishes ports | local dev, or a host where vlotr owns :80/:443 |
| `infra/compose.tunnel.yaml` | none — your existing `cloudflared` is the only ingress | the host already runs a Cloudflare Tunnel |

The `worker` reuses the API image, so only two images exist: `vlotr-api`, `vlotr-web`.

## First run: the setup wizard (there is no seed step)

A fresh install has an empty database. Open the app in a browser and every route lands on
`/setup` — the first-run wizard creates the organization, its branding/locale/modules, and
the **owner account** in one step. The owner is also the **instance owner**
(`users.is_superuser`): whoever installs the box operates it. The wizard closes permanently
the moment the first org exists.

The hostname you run the wizard on is **claimed as the org's verified custom domain**
(unless it is already `<slug>.<VLOTR_BASE_DOMAIN>`), because hostname → org resolution is
strict — see below. So run the wizard on the address you intend to keep.

The old `VLOTR_SEED_*` variables are gone; leftover values in a stack's environment are
ignored.

## Hostname resolution is strict (upgrade note)

A request resolves to an org in exactly two ways: a **verified custom domain**, or
`<slug>.<VLOTR_BASE_DOMAIN>`. An unknown hostname is a 404 — there is **no fallback to "the
only org"** anymore (issue #26): a fallback would serve tenant data on any typo'd or
hijacked hostname.

**Upgrading an existing install:** the migration keeps you resolving.

- A custom domain already present in `org_settings` is moved to `orgs` and grandfathered
  as verified.
- A single-org install with no custom domain gets **`app.<VLOTR_BASE_DOMAIN>`** claimed as
  its verified domain — the hostname both compose files serve the app on.

If you serve vlotr on any *other* hostname (e.g. `crm.agency.nl` while
`VLOTR_BASE_DOMAIN=agency.nl`), that host stopped resolving with this release. Fix: sign in
via `app.<base domain>` (or `<slug>.<base domain>`) once, then set your real hostname under
*Instellingen → Huisstijl → Eigen domein* — it activates after a DNS TXT verification.
Alternatively set it directly in the database (`orgs.custom_domain` +
`orgs.custom_domain_verified_at = now()`).

For local dev without Traefik (`pnpm dev` + local API): browse `http://vlotr.localhost:5173`
(slug + base domain `localhost`), not bare `localhost` — or run the wizard on the host you
prefer and it claims it.

## Instance administration (off by default)

`VLOTR_INSTANCE_ADMIN_ENABLED=true` opens `/instance` (and `/api/v1/instance/*`) to
**instance owners** (`users.is_superuser`): org lifecycle (create, rename, re-slug,
suspend, soft-delete, hard-delete), per-org module toggles, per-org **export/import**, an
**audit log**, and time-boxed, bannered **impersonation**. It ships disabled because a
cross-tenant surface on a single-tenant box is pure attack surface; the API answers 404
while it is off. Every mutation lands in `instance_audit_log`.

Hard delete refuses to run without an export taken *after* the soft delete — that export
(a JSON file with every row of the org) is the only copy that remains. Keep it somewhere
safe; the same file can be imported again on this or another instance running the **same
release** (imports across schema revisions are rejected).

## Single sign-on (OIDC, off by default)

Federates login to an external IdP (Authentik, Keycloak, Entra ID, …). Register the app
there with callback URL `https://<your-host>/api/v1/auth/oidc/callback`, then set:

| Variable | Default | Meaning |
|---|---|---|
| `VLOTR_OIDC_ENABLED` | `false` | Mount the SSO routes and show the SSO button on the login page. |
| `VLOTR_OIDC_DISCOVERY_URL` | — | The IdP's `/.well-known/openid-configuration` URL. **Required when enabled.** |
| `VLOTR_OIDC_CLIENT_ID` | — | Client id registered at the IdP. **Required when enabled.** |
| `VLOTR_OIDC_CLIENT_SECRET` | — | Client secret. **Required when enabled.** |
| `VLOTR_OIDC_ENFORCED` | `false` | Disable local username/password login; SSO becomes the only way in. |
| `VLOTR_OIDC_NAME` | `sso` | Internal client name; cosmetic. |
| `VLOTR_OIDC_AUTO_PROVISION_MEMBERSHIP` | `true` | First SSO login auto-grants a membership in the resolved org. Set `false` to require an explicit invite first. |
| `VLOTR_OIDC_DEFAULT_ROLE` | `member` | Role granted by auto-provisioning. |

All three of discovery URL, client id and client secret must be set (non-empty) for OIDC
to be considered configured — one gate covers both the routes and the login-page button,
so a half-configured instance never shows an SSO button that 404s (issue #6). If
`VLOTR_OIDC_ENABLED=true` with any of them missing, the API logs a startup `WARNING`
naming the missing variables and runs with local login only. If `VLOTR_OIDC_ENFORCED=true`
with any of them missing, the API **refuses to start** — enforced OIDC turns local login
off, so booting anyway would lock every user out.

## Releases and image tags

Images are built **only** when a `v*` tag is pushed (`.github/workflows/release.yml`). Pushing
to `main` builds nothing.

```bash
git tag -a v1.2.3 -m "..." && git push origin v1.2.3
```

That publishes `1.2.3`, `1.2`, `latest`, and `sha-<commit>`, and opens a GitHub Release.
`latest` follows the newest **stable** tag; a pre-release (`v1.2.3-rc.1`) never moves it.

**Pin `VLOTR_TAG` to an exact version in production.** `latest` is a reasonable default for a
fresh install and a poor one for a host you upgrade — a redeploy would silently pull a newer
app than the one you tested.

## Private GHCR

The repo is private, so the images are too, and the host needs credentials to pull. In
Portainer: *Registries → Add registry → Custom registry*, URL `ghcr.io`, username = your
GitHub user, password = a **classic** PAT with only the `read:packages` scope. Portainer
matches it by hostname, so every stack pulling `ghcr.io/vlotr-crm/*` picks it up — nothing
references it in the compose file.

Fine-grained tokens have had patchy GHCR support and fail with an opaque 403. Prefer a
dedicated machine user over a personal PAT: if that account leaves the org or the token
rotates, production stops pulling on its next redeploy.

Docker re-authenticates on every pull, so an expired token surfaces as a failed *redeploy*,
not a failed install.

## Deploy the stack from Git, not the web editor

Portainer → *Stacks → Add stack → Repository*. Set the secrets under the stack's Environment
variables. Required (no defaults): `POSTGRES_ADMIN_PASSWORD`, `APP_DB_PASSWORD`,
`VLOTR_SECRET_KEY`, `VLOTR_BASE_DOMAIN`. The org and its owner are created in the browser
by the first-run wizard, not by environment variables.

Pasting YAML into the web editor works, but relative paths in the file have no host directory
to resolve against — Docker silently creates an *empty* one rather than failing. Deploying
from the repository avoids that whole class of bug.

For `compose.tunnel.yaml`, the external `tunnel` network must already exist (`docker network
ls`) — it's the one your `cloudflared` stack created. `cloudflared` routes `/api/*` and
`/mcp/*` to `http://api:8000` and everything else to `http://web:3000`; see
`infra/cloudflared/config.yml`.

> `infra/cloudflared/*.json` is a long-lived tunnel credential and is gitignored. Never
> commit it.

## The application database role

`db-init` is a one-shot service that creates `vlotr_app`, the **non-superuser** role the API
and worker connect as. It must be non-superuser or Postgres RLS is bypassed and tenant
isolation is gone (CLAUDE.md §5); it owns the `public` schema so Alembic can create tables and
policies while still being subject to `FORCE ROW LEVEL SECURITY`.

It runs on every `up`, is idempotent, and reads nothing from disk. `api` waits on it via
`service_completed_successfully`, because `alembic upgrade head` runs before uvicorn binds —
if the role is missing, the API exits and the healthcheck can never pass.

It is deliberately **not** a `/docker-entrypoint-initdb.d` script: those run only when `PGDATA`
is empty, so they never repair an existing volume, and they require a file on the host.

## Health endpoints

Three surfaces, kept apart on purpose — they have different callers and different threat models.

| Endpoint | Auth | Checks | Use |
|---|---|---|---|
| `GET /health` | none | nothing | Liveness. Compose/orchestrator probes. Must stay cheap: a probe that touched Postgres would restart a healthy API whenever the database blipped. |
| `GET /health/ready` | none | Postgres, Redis, Alembic at head | Readiness. `200 {"status":"ok"}` or `503 {"status":"degraded"}`. Deliberately **detail-free** — it never names the failing dependency, because anyone can call it. |
| `GET /api/v1/system/info` | owner/admin | everything, in detail | The Instellingen → Systeem screen. Versions, git sha, migration revisions, worker heartbeat, queue depth. Gated because exact versions and dependency topology are reconnaissance. |

The container healthcheck stays on `/health`. Point a *readiness* gate (a load balancer, or
`depends_on: service_healthy` for something that must not start against a half-migrated box)
at `/health/ready`.

**Pending migrations are visible.** `up_to_date: false` on a running API means the schema is
behind the code — the entrypoint's `alembic upgrade head` was skipped or failed, not that it is
still in flight (it runs *before* uvicorn binds).

**A dead worker is otherwise invisible.** The API keeps serving and ARQ jobs silently pile up.
The worker writes a heartbeat to Redis every minute; `system/info` reports its last check-in
and the queue depth.

## Version stamping

`VLOTR_VERSION`, `VLOTR_GIT_SHA` and `VLOTR_BUILT_AT` are baked into both images at build time
(`.github/workflows/release.yml` passes them as build args) and re-exported as OCI labels, so
`docker inspect` and the Systeem screen can never disagree:

```bash
docker inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' \
  ghcr.io/vlotr-crm/vlotr-api:1.2.3
```

A source checkout reports `0.0.0+dev`. That sorts below every release, so the update check
stays quiet rather than claiming an update is always available.

## Update check (and how to switch it off)

A daily cron in the `worker` asks the **public GitHub Releases API** for the newest stable tag
of `vlotr-crm/vlotr`, caches the answer in Redis, and the Systeem screen shows a notice when a
newer release exists. It **never auto-updates** — pulling a new tag stays a human decision.

What leaves the box: one unauthenticated `GET` to
`https://api.github.com/repos/vlotr-crm/vlotr/releases/latest`. Nothing is sent about the
instance — not its version, not its org, not a ping. There is no telemetry.

It is an **instance** setting, not a per-tenant one: one box makes one call and the answer (a
version number) is identical for every org on it.

```yaml
# infra/compose.tunnel.yaml → stack environment
VLOTR_UPDATE_CHECK_ENABLED: "false"     # no outbound update traffic at all
```

With it off, the Systeem screen says so and shows no update state. Restart `worker` and `api`
after changing it.

## Troubleshooting

The API entrypoint runs `alembic upgrade head` under `set -e` before starting uvicorn. So a
database problem shows up as `api` **unhealthy** or restarting, never as a running-but-broken
API. Always read the logs rather than the health status:

```bash
docker logs --timestamps --tail 40 vlotr-api-1
```

**`socket.gaierror: Temporary failure in name resolution`** — `api` cannot resolve `db`; they
are on different networks. Compose only puts everything on `default` when *no* service names a
network; as soon as one does, services that omit `networks:` land on `default` instead. Every
service in `compose.tunnel.yaml` must therefore name `vlotr` explicitly. Check with:

```bash
docker inspect -f '{{.Name}} -> {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
  $(docker ps -aq --filter name=vlotr)
```

**`InvalidPasswordError: password authentication failed for user "vlotr_app"`** — the role is
missing or its password differs from `APP_DB_PASSWORD`. Re-running the stack fixes this now
that `db-init` exists. Do **not** delete the `db-data` volume to "reinitialise" it; that
destroys the database and was never necessary.

**Editing a service that Compose sees as unchanged** does not recreate its container. After
changing networks or environment, force it: Portainer's *Re-pull image and redeploy*, or
`docker compose -f compose.tunnel.yaml up -d --force-recreate`.
