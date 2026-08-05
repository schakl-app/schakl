# Deploying schakl

Self-hosted, single-org install (CLAUDE.md §5). Two Compose files, both self-contained:

| File | Ingress | Use when |
|---|---|---|
| `infra/compose.yaml` | Traefik, publishes ports | local dev, or a host where schakl owns :80/:443 |
| `infra/compose.tunnel.yaml` | none — your existing `cloudflared` is the only ingress | the host already runs a Cloudflare Tunnel |

The `worker` reuses the API image, so only two images exist: `schakl-api`, `schakl-web`.

## First run: the setup wizard (there is no seed step)

A fresh install has an empty database. Open the app in a browser and every route lands on
`/setup` — the first-run wizard creates the organization, its branding/locale/modules, and
the **owner account** in one step. The owner is also the **instance owner**
(`users.is_superuser`): whoever installs the box operates it. The wizard closes permanently
the moment the first org exists.

The hostname you run the wizard on is **claimed as the org's verified custom domain**
(unless it is already `<slug>.<SCHAKL_BASE_DOMAIN>`), because hostname → org resolution is
strict — see below. So run the wizard on the address you intend to keep.

The old `SCHAKL_SEED_*` variables are gone; leftover values in a stack's environment are
ignored.

## Hostname resolution is strict (upgrade note)

A request resolves to an org in exactly two ways: a **verified custom domain**, or
`<slug>.<SCHAKL_BASE_DOMAIN>`. An unknown hostname is a 404 — there is **no fallback to "the
only org"** anymore (issue #26): a fallback would serve tenant data on any typo'd or
hijacked hostname.

**Upgrading an existing install:** the migration keeps you resolving.

- A custom domain already present in `org_settings` is moved to `orgs` and grandfathered
  as verified.
- A single-org install with no custom domain gets **`app.<SCHAKL_BASE_DOMAIN>`** claimed as
  its verified domain — the hostname both compose files serve the app on.

If you serve schakl on any *other* hostname (e.g. `crm.agency.nl` while
`SCHAKL_BASE_DOMAIN=agency.nl`), that host stopped resolving with this release. Fix: sign in
via `app.<base domain>` (or `<slug>.<base domain>`) once, then set your real hostname under
*Instellingen → Huisstijl → Eigen domein* — it activates after a DNS TXT verification.
Alternatively set it directly in the database (`orgs.custom_domain` +
`orgs.custom_domain_verified_at = now()`).

For local dev without Traefik (`pnpm dev` + local API): browse `http://schakl.localhost:5173`
(slug + base domain `localhost`), not bare `localhost` — or run the wizard on the host you
prefer and it claims it.

## Roles and permissions (upgrade note — members lose write access)

This release replaces the fixed four-role enum with **tenant-defined roles carrying explicit
permissions** (issue #19). The migration seeds your existing four roles — `owner`, `admin`,
`member`, `client` — and maps every existing membership onto the one it already had. Nothing
is deleted, and `owner`/`admin`/`client` behave exactly as before.

**`member` does not.** Its new default is deliberately restrictive: read everything, plus

- create a task, and edit a task **assigned to them**;
- comment on tasks;
- log their **own** hours;
- request their **own** leave.

So on `alembic upgrade head` — which the API entrypoint runs unattended, before uvicorn binds
— every `member` at your agency **loses** the ability to:

| | |
|---|---|
| create / edit / delete | a company, a contact, a project |
| edit | a task they are not the assignee of |
| create / edit | task labels, checklist templates, task templates |
| apply | a task template to a client |
| create / edit | leave types |

This is a stricter default posture, not a removal: **every one of these is a checkbox** in
*Instellingen → Rollen*. Nobody is locked out of their own work, and no data is touched.

**Before you upgrade**

1. **Back up the database.** `docker compose exec db pg_dump -U schakl schakl > backup.sql`.
   This migration is reversible (`alembic downgrade -1` drops the three new tables and the new
   `org_settings` column, leaving `memberships.role` exactly as it was), but take the backup
   anyway.
2. Note who is a `member` today: *Instellingen → Gebruikers*.

**After you upgrade**

1. Sign in as an `owner` or `admin` and open *Instellingen → Rollen*.
2. Either tick the permissions your `member` role should keep — the matrix is grouped per
   module, with a *select all* per module — or **duplicate** `member` into a custom role
   (e.g. *Senior medewerker*), grant it what you need, and assign it on
   *Instellingen → Gebruikers*. A user may hold several roles; their permissions are the union.
3. `owner` always holds `*` and cannot be edited or deleted. The other three system roles
   cannot be deleted or renamed, but their permissions **are** editable.

**Rolling back to the previous image** is safe for one release: `memberships.role` is still
written on every role change (highest privilege wins when a user holds several system roles),
so the old code keeps reading a value it understands. For that reason this release also refuses
to give anyone *only* custom roles — every membership keeps at least one system role. That
restriction lifts when `memberships.role` is dropped, one release later.

A module that ships **after** your org was created (say, `subscriptions`) brings its own
permissions. The API grants them to your system roles once, at boot, and records that it did
so in `org_settings.applied_permission_defaults` — a permission you unticked stays unticked.

## Instance administration (off by default)

`SCHAKL_INSTANCE_ADMIN_ENABLED=true` opens `/instance` (and `/api/v1/instance/*`) to
**instance owners** (`users.is_superuser`): org lifecycle (create, rename, re-slug,
suspend, soft-delete, hard-delete), per-org module toggles, per-org **export/import**, an
**audit log**, and time-boxed, bannered **impersonation**. It ships disabled because a
cross-tenant surface on a single-tenant box is pure attack surface; the API answers 404
while it is off. Every mutation lands in `instance_audit_log`.

Hard delete refuses to run without an export taken *after* the soft delete — that export
(a JSON file with every row of the org) is the only copy that remains. Keep it somewhere
safe; the same file can be imported again on this or another instance running the **same
release** (imports across schema revisions are rejected).

## Public demo mode (issue #141, off by default)

`SCHAKL_DEMO_MODE=true` turns the instance into a **public, publicly-writable demo**. This is a
posture, not a feature toggle: when on it **forces** the safe values regardless of the rest of the
env — registration off, instance admin off (so impersonation off too), and `/setup` locked. On top
of RLS (tenant isolation) and RBAC (capability), a central **demo-guard** catalog
(`app/core/demo/guard.py`) rejects the operations that are dangerous specifically because anyone on
the internet can reach them, with an `errors.demo_blocked` envelope:

- outbound side effects (Google OAuth connect, Drive/Calendar writes);
- credential surfaces (API-key minting, SSO settings, password/email change);
- instance identity (the whole `/instance` surface, custom-domain claim/verification);
- uploads (a public file box is a malware host).

Ordinary tenant editing — companies, contacts, projects, tasks, time, leave — stays fully open, so
the demo actually demonstrates the product. A persistent, dismissal-proof banner tells visitors it
is a demo and how often it resets (`SCHAKL_DEMO_RESET_MINUTES`, default 60).

**Never point the open internet at this without hardening it:** put Cloudflare in front, add a
Traefik per-IP rate-limit middleware, keep **no SMTP credentials on the box** (defence in depth
behind the email guard), and `noindex` the app host. Resets are cheap (one org's rows), so a small
box is fine.

> The data lifecycle — a curated seed org, a golden snapshot, and the periodic reset cron that wipes
> the demo back to it — reuses the #26 export/import format and is tracked as the remaining half of
> #141; the guard and forced posture above are already in effect whenever the flag is on.

## Single sign-on (OIDC, off by default)

Federates login to an external IdP (Authentik, Keycloak, Entra ID, Google, …). Since #76 this
is **configured in the app, per organization** — Instellingen → Single sign-on — not with
environment variables: client id, discovery URL, display name, JIT-provisioning policy, the
enabled/enforced toggles, and the client secret (encrypted at rest with a key derived from
`SCHAKL_ENCRYPTION_KEY`, falling back to `SCHAKL_SECRET_KEY`). Changes apply immediately, no
restart. The settings page shows the exact callback URL to register at the IdP and offers a
**Test connection**; "require SSO" cannot be switched on until a test has succeeded. Provider
walkthroughs, the exact-match rules for the callback URL, and the `redirect_uri_mismatch` fix
live in [`SSO.md`](SSO.md).

The old `SCHAKL_OIDC_*` variables are retired: the migration that ships #76 reads them **once**
at upgrade time and seeds each org's row from them (secret stored encrypted), after which the
app ignores them — remove them from your compose file at leisure. One auth-related variable
remains:

| Variable | Default | Meaning |
|---|---|---|
| `SCHAKL_FORCE_LOCAL_LOGIN` | `false` | **Break-glass.** Re-enables local password login regardless of any org's "require SSO" setting — for when the IdP is broken or misconfigured and nobody can sign in. Set it, sign in locally, fix or disable SSO in Instellingen → Single sign-on, then unset it. |

## File storage (the second stateful thing)

Uploaded files (issue #123 — avatars, task attachments, branding assets) live on the named
volume **`storage-data`**, mounted into `api` and `worker` at `SCHAKL_STORAGE_PATH`
(`/data/storage`). Postgres is no longer the only state on the box:

- **Back up `storage-data` alongside the database.** A restored DB without the volume leaves
  `files` rows whose bytes are gone (the API then serves 404 for them); a restored volume
  without the DB leaves orphaned bytes. Snapshot both together.
- **Node-local by design.** A single volume is right for the one-host Compose deploy; a future
  multi-node/cloud deploy swaps the storage backend (`SCHAKL_STORAGE_BACKEND`), not the callers.
- **Limits are instance config:** `SCHAKL_UPLOAD_MAX_BYTES` (default 10 MB) and
  `SCHAKL_UPLOAD_ALLOWED_TYPES` (a JSON list; defaults to images, PDF, text, zip and office
  documents). The API refuses anything outside them with `413`/`422`.
- **Identical bytes are stored once, per org** — see `docs/STORAGE.md` for the model. Two
  consequences for operators: deleting a file no longer frees space immediately (a nightly
  cron reclaims it after `SCHAKL_STORAGE_BLOB_GRACE_HOURS`, default 24), and existing
  duplicates are folded in batches of `SCHAKL_STORAGE_FOLD_BATCH` (default 500) per org per
  night, so a large instance reclaims its backlog over several days rather than at upgrade.
  Both are `worker` settings; the cron runs at 03:15 UTC.

### S3-compatible object storage (issue #190, off by default)

Instead of the node-local volume, new files can go to any S3-compatible bucket (Hetzner
Object Storage, MinIO, Scaleway, AWS — the implementation codes strictly against the S3 API).
Instance-wide, via environment variables on `api` **and** `worker`:

```bash
SCHAKL_STORAGE_BACKEND=s3
SCHAKL_STORAGE_S3_ENDPOINT=https://fsn1.your-objectstorage.com
SCHAKL_STORAGE_S3_REGION=fsn1
SCHAKL_STORAGE_S3_BUCKET=my-schakl-files
SCHAKL_STORAGE_S3_ACCESS_KEY_ID=…
SCHAKL_STORAGE_S3_SECRET_ACCESS_KEY=…
SCHAKL_STORAGE_S3_KEY_PREFIX=            # optional, nests all keys under a prefix
SCHAKL_STORAGE_S3_FORCE_PATH_STYLE=true  # default; MinIO-safe, Hetzner supports both
```

What to know before flipping it on:

- **Override, not migration.** The backend is recorded per file row, so enabling S3 affects
  **new writes only** — files already on `storage-data` keep serving from the volume. Keep
  the volume (and its backups) as long as any `local` rows exist; `GET /api/v1/files` rows
  carry `backend`, so "what still lives on the volume" is answerable.
- **The bucket stays private.** No public ACL or bucket policy — every byte still travels the
  API (tenant scoping + permissions, Golden Rule 6). Object keys are org-prefixed
  (`<org_id>/<file_id>`), so isolation holds at the key level too.
- **Scope the credential to the one bucket**, and prefer bucket versioning + lifecycle rules
  for backup/retention over volume snapshots for the S3-backed rows.
- **Changing the bucket or prefix later** makes objects written under the old one unreachable
  from the app (the same as repointing `SCHAKL_STORAGE_PATH`). Rows whose backend can't be
  reached (e.g. S3 config removed) answer a distinct 404, `errors.storage_backend_unavailable`,
  naming the fix.
- **Rollback is safe:** unset the variables and new writes fall back to the volume; S3 rows
  then 404 (distinctly) until the config returns. No migration in either direction.
- **Two prefixes are not tenant data.** `archive/<org_id>/…` holds org archives (rows + bytes,
  written before a cloud termination — see `docs/CLOUD.md`). It sits outside every org's key
  space on purpose, so deleting a terminated org's `<org_id>/` prefix cannot take the archive
  of that same org with it. Exclude it from any retention rule you would not want applied to
  your only copy of a departed customer's data.

## Credentials from files (`SCHAKL_<SETTING>_FILE`)

Any setting can be read from a file instead of an environment variable:

```
SCHAKL_SECRET_KEY_FILE=/run/secrets/schakl_secret_key
SCHAKL_DATABASE_URL_FILE=/run/secrets/schakl_database_url
SCHAKL_STORAGE_S3_SECRET_ACCESS_KEY_FILE=/run/secrets/schakl_s3_secret
```

This exists because **a Docker secret is a file, not an environment variable**. Creating a
secret named after a setting mounts something the app would otherwise never look at, and the
setting silently keeps its default — a failure invisible until the feature is used, when an
unset S3 key surfaces as a broken upload rather than a container that refuses to start.

Three rules, each so a mistake is loud rather than silent:

- **The direct variable wins.** `SCHAKL_X` alongside `SCHAKL_X_FILE` uses the direct value, so
  a stale `_FILE` left in a stack cannot break a working deployment.
- **Unreadable or empty refuses the boot**, naming the path and pointing at the secret
  attachment. Setting `_FILE` says the value comes from a secret; falling back to the default
  would be a silent downgrade.
- **An unknown setting refuses the boot.** `SCHAKL_STORAGE_S3_KEY_FILE` is a typo for
  `..._ACCESS_KEY_ID_FILE`, not a request to ignore it.

`infra/compose.portainer.yml` uses this for all five credentials (signing key, database URL,
both S3 keys, the Cloudflare token), so none of them appears in the stack definition, in
Portainer's database, or in `docker service inspect` — only the paths do.

## Releases and image tags

Images are built **only** when a `v*` tag is pushed (`.github/workflows/release.yml`). Pushing
to `main` builds nothing.

```bash
git tag -a v1.2.3 -m "..." && git push origin v1.2.3
```

That publishes `1.2.3`, `1.2`, `latest`, and `sha-<commit>`, and opens a GitHub Release.
`latest` follows the newest **stable** tag; a pre-release (`v1.2.3-rc.1`) never moves it.

Each tag is a **multi-arch manifest list** covering `linux/amd64` (x86-64) and `linux/arm64`
(ARM) — so the same image runs on an Intel/AMD host and on ARM (Hetzner Ampere/CAX, AWS
Graviton, Apple Silicon). `docker pull` selects the variant matching the host automatically;
no per-arch tag and no compose change is needed. Confirm what a tag carries with:

```bash
docker buildx imagetools inspect ghcr.io/schakl-app/schakl-api:1.2.3
```

**Pin `SCHAKL_TAG` to an exact version in production.** `latest` is a reasonable default for a
fresh install and a poor one for a host you upgrade — a redeploy would silently pull a newer
app than the one you tested.

## Private GHCR

The images on GHCR may require credentials to pull (their visibility is a package
setting, independent of the now-public repo). If a pull is denied: in
Portainer: *Registries → Add registry → Custom registry*, URL `ghcr.io`, username = your
GitHub user, password = a **classic** PAT with only the `read:packages` scope. Portainer
matches it by hostname, so every stack pulling `ghcr.io/schakl-app/*` picks it up — nothing
references it in the compose file.

Fine-grained tokens have had patchy GHCR support and fail with an opaque 403. Prefer a
dedicated machine user over a personal PAT: if that account leaves the org or the token
rotates, production stops pulling on its next redeploy.

Docker re-authenticates on every pull, so an expired token surfaces as a failed *redeploy*,
not a failed install.

## Deploy the stack from Git, not the web editor

Portainer → *Stacks → Add stack → Repository*. Set the secrets under the stack's Environment
variables. Required (no defaults): `POSTGRES_ADMIN_PASSWORD`, `APP_DB_PASSWORD`,
`SCHAKL_SECRET_KEY`, `SCHAKL_BASE_DOMAIN`. The org and its owner are created in the browser
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

`db-init` is a one-shot service that creates `schakl_app`, the **non-superuser** role the API
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
| `GET /healthz` (**web**) | none | nothing | Liveness for the SSR container. Answers "is Node listening yet?". Resolves no tenant and **never calls the API** — a probe that did would pull every web replica out of rotation during an API restart, turning a blip into an outage. |

The container healthcheck stays on `/health`. Point a *readiness* gate (a load balancer, or
`depends_on: service_healthy` for something that must not start against a half-migrated box)
at `/health/ready`.

**Pending migrations are visible.** `up_to_date: false` on a running API means the schema is
behind the code — the entrypoint's `alembic upgrade head` was skipped or failed, not that it is
still in flight (it runs *before* uvicorn binds).

**A dead worker is otherwise invisible.** The API keeps serving and ARQ jobs silently pile up.
The worker writes a heartbeat to Redis every minute; `system/info` reports its last check-in
and the queue depth.

## Rolling updates: a redeploy is not an outage

**The symptom this section exists to explain: every cloud redeploy used to answer `500` for its
whole duration**, on every page including login.

The cause was a chain, not a bug in any one place. The API entrypoint runs `alembic upgrade head`
before serving, so the swarm stacks pinned it to `replicas: 1` with `order: stop-first` — two
tasks booting together would have raced each other through the same revisions. But one replica
plus stop-first is, by definition, a window with **zero** API tasks: Swarm stops the only one
before it starts its replacement. Meanwhile `web` rolls `start-first` and stays up — so it was
still serving, straight into nothing. Its first server hook fetches `/meta/tenant` before any page
renders, the fetch throws, and nothing catches it. The web app was up precisely so that it could
render an error.

**The fix names the real constraint.** It was never "one replica"; it was "one migration at a
time". That is now a Postgres advisory lock taken by `alembic upgrade` itself
(`app/core/migrations.py`), so:

- the API runs **`replicas: 2` + `order: start-first`** — a new task boots, migrates, passes its
  healthcheck, and only then does Swarm retire an old one. Something is always serving.
- whoever loses the lock **waits and then no-ops**, because by the time it runs, the schema is
  already at head. It logs `waiting for the migration lock`, so a slow deploy says so.
- the lock lives in `alembic/env.py`, not in `docker-entrypoint.sh`, so an operator running
  `alembic upgrade head` by hand mid-deploy contends for it too.
- a migration killed mid-flight cannot wedge the next deploy: the lock rides its own connection,
  and Postgres drops session locks when the backend goes away.

**This works because destructive schema changes already go out over two releases**
(`docs/WORKFLOW.md`, expand/contract). Start-first means old and new code share a schema for the
length of the rollover — which is exactly the property expand/contract guarantees. If you are
about to break that rule, you are also about to break rolling deploys.

Three settings are load-bearing and easy to get wrong:

| Setting | Why |
|---|---|
| `monitor: 180s` on the API | Swarm's default is `5s` — shorter than a boot that includes a migration, so it would declare the update good before the first task had finished and `failure_action: rollback` would never fire. |
| `SCHAKL_DB_POOL_SIZE` / `_MAX_OVERFLOW` | The pool is **per replica**. Both cloud stacks halve them to `8`/`8` so two replicas total 32 connections against the 30 one replica used — the ceiling did not silently double on a managed database sized for one. Raise them only together with `API_REPLICAS`, after checking `max_connections`. |
| The `web` healthcheck | Without one, `start-first` rotates a new web task into Traefik the moment the container is *running*, a second or two before the SSR server binds `:3000` — a handful of 502s per deploy. It probes `/healthz` with `node -e` (the runtime image is `node:22-slim`, which has neither curl nor wget). |

`SCHAKL_MIGRATION_LOCK_TIMEOUT_SECONDS` (default `600`) bounds how long a booting instance waits
for another one's migration. It bounds a *rolling deploy*, not a migration — the holder may run as
long as it likes. On timeout the task fails its healthcheck and Swarm rolls back, which is the
right outcome: the alternative is a container that never serves and never explains itself.

**Self-hosted (`infra/compose.yaml`) is deliberately unchanged** — one replica, and `api` still
migrates on boot. A self-hosted release upgrades itself unattended, so moving migrations into a
separate service would have broken every existing compose file on the next `docker compose pull
&& up -d`. The lock is simply inert when nothing contends for it.

There is also a `migrate` entrypoint command (`docker run --rm … migrate`) for an operator who
would rather land the schema as an explicit step before rolling the service. It is optional, and
safe to run *during* a rolling deploy — it takes the same lock.

## Version stamping

`SCHAKL_VERSION`, `SCHAKL_GIT_SHA` and `SCHAKL_BUILT_AT` are baked into both images at build time
(`.github/workflows/release.yml` passes them as build args) and re-exported as OCI labels, so
`docker inspect` and the Systeem screen can never disagree:

```bash
docker inspect -f '{{index .Config.Labels "org.opencontainers.image.version"}}' \
  ghcr.io/schakl-app/schakl-api:1.2.3
```

A source checkout reports `0.0.0+dev`. That sorts below every release, so the update check
stays quiet rather than claiming an update is always available.

## Update check (and how to switch it off)

A daily cron in the `worker` asks the **public GitHub Releases API** for the newest stable tag
of `schakl-app/schakl`, caches the answer in Redis, and the Systeem screen shows a notice when a
newer release exists. It **never auto-updates** — pulling a new tag stays a human decision.

What leaves the box: one unauthenticated `GET` to
`https://api.github.com/repos/schakl-app/schakl/releases/latest`. Nothing is sent about the
instance — not its version, not its org, not a ping. There is no telemetry.

It is an **instance** setting, not a per-tenant one: one box makes one call and the answer (a
version number) is identical for every org on it.

```yaml
# infra/compose.tunnel.yaml → stack environment
SCHAKL_UPDATE_CHECK_ENABLED: "false"     # no outbound update traffic at all
```

With it off, the Systeem screen says so and shows no update state. Restart `worker` and `api`
after changing it.

## Troubleshooting

The API entrypoint runs `alembic upgrade head` under `set -e` before starting uvicorn. So a
database problem shows up as `api` **unhealthy** or restarting, never as a running-but-broken
API. Always read the logs rather than the health status:

```bash
docker logs --timestamps --tail 40 schakl-api-1
```

**`socket.gaierror: Temporary failure in name resolution`** — `api` cannot resolve `db`; they
are on different networks. Compose only puts everything on `default` when *no* service names a
network; as soon as one does, services that omit `networks:` land on `default` instead. Every
service in `compose.tunnel.yaml` must therefore name `schakl` explicitly. Check with:

```bash
docker inspect -f '{{.Name}} -> {{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' \
  $(docker ps -aq --filter name=schakl)
```

**`InvalidPasswordError: password authentication failed for user "schakl_app"`** — the role is
missing or its password differs from `APP_DB_PASSWORD`. Re-running the stack fixes this now
that `db-init` exists. Do **not** delete the `db-data` volume to "reinitialise" it; that
destroys the database and was never necessary.

**Editing a service that Compose sees as unchanged** does not recreate its container. After
changing networks or environment, force it: Portainer's *Re-pull image and redeploy*, or
`docker compose -f compose.tunnel.yaml up -d --force-recreate`.

## Licensed modules (issue #137)

The core platform is free to use. A small set of extension modules — currently `leave` and
the MCP server — requires a **license key**, installed by the instance owner under
*Instellingen → Licentie* (`PUT /api/v1/instance/license`). Validation is **fully offline**
against a public key baked into the image: the box never phones home.

- Every fresh install or upgrade to this release starts a **built-in trial window**
  (`SCHAKL_LICENSE_BOOTSTRAP_GRACE_DAYS`, default 14): licensed modules work fully without
  a key. After it, they cannot be newly enabled, and already-enabled ones turn read-only.
- An **expired** license keeps working through its grace period, then its modules turn
  **read-only**: mutations answer `402 errors.license_expired`, reads and CSV exports keep
  working forever. Installing a new key restores everything instantly — no data is touched.
- `SCHAKL_LICENSE_PUBLIC_KEY` overrides the baked-in verification key (key rotation).
