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
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000
    ;;
  worker)
    exec arq app.worker.WorkerSettings
    ;;
  *)
    exec "$@"
    ;;
esac
