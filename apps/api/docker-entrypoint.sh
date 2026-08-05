#!/bin/sh
# Entrypoint for the api/worker containers.
#   api     → run migrations, then serve (a fresh install is provisioned via the
#             first-run wizard at /setup — there is no seed step; issue #26)
#   worker  → run the ARQ worker
#   migrate → run migrations and exit
set -e

# `api` still migrates before serving, and that is deliberate: a self-hosted release upgrades
# itself unattended (docs/WORKFLOW.md), so moving the migration into a separate service would
# break every existing compose file on the next `docker compose pull && up -d`.
#
# It is nonetheless safe to run several API replicas now — `alembic upgrade` takes a Postgres
# advisory lock (apps/api/alembic/env.py), so concurrent boots serialise instead of racing.
# That is what lets the cloud stacks use `order: start-first` and stop taking a full API outage
# on every redeploy.
case "$1" in
  api)
    echo "→ applying database migrations"
    alembic upgrade head
    echo "→ starting API"
    # Trust the reverse proxy's X-Forwarded-* headers so generated URLs use the external
    # scheme/host. Without this the app sees the internal http hop and builds http:// URLs —
    # which breaks the OIDC redirect_uri (Google rejects http for public hosts). Only the
    # proxy can reach this port, so trusting all forwarded IPs is safe here (see docs/SSO.md).
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="*"
    ;;
  worker)
    exec arq app.worker.WorkerSettings
    ;;
  migrate)
    # Migrate and exit, for an operator who would rather land the schema as an explicit step
    # before rolling the service (`docker run --rm … migrate`). Optional — `api` still migrates
    # on boot — and safe to run *during* a rolling deploy: it contends for the same advisory
    # lock as every booting replica.
    echo "→ applying database migrations"
    exec alembic upgrade head
    ;;
  *)
    exec "$@"
    ;;
esac
