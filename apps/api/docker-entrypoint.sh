#!/bin/sh
# Entrypoint for the api/worker containers.
#   api    → run migrations, seed the single org (idempotent), then serve
#   worker → run the ARQ worker
set -e

case "$1" in
  api)
    echo "→ applying database migrations"
    alembic upgrade head
    echo "→ seeding (idempotent)"
    python -m app.seed || echo "seed skipped/failed (continuing)"
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
