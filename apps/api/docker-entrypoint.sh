#!/bin/sh
# Entrypoint for the api/worker containers.
#   api    → run migrations, then serve (a fresh install is provisioned via the
#            first-run wizard at /setup — there is no seed step; issue #26)
#   worker → run the ARQ worker
set -e

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
  *)
    exec "$@"
    ;;
esac
