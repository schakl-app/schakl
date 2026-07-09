# Deploying vlotr

Self-hosted, single-org install (CLAUDE.md §5). Two Compose files, both self-contained:

| File | Ingress | Use when |
|---|---|---|
| `infra/compose.yaml` | Traefik, publishes ports | local dev, or a host where vlotr owns :80/:443 |
| `infra/compose.tunnel.yaml` | none — your existing `cloudflared` is the only ingress | the host already runs a Cloudflare Tunnel |

The `worker` reuses the API image, so only two images exist: `vlotr-api`, `vlotr-web`.

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
`VLOTR_SECRET_KEY`, `VLOTR_BASE_DOMAIN`, `VLOTR_SEED_ADMIN_EMAIL`,
`VLOTR_SEED_ADMIN_PASSWORD`.

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
