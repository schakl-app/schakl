#!/bin/bash
# Runs once on first DB init. Creates the NON-superuser application role that the API/worker
# connect as, so Postgres RLS is actually enforced (superusers bypass RLS). The role owns the
# public schema so it can run migrations (create tables, policies) while remaining subject to
# FORCE ROW LEVEL SECURITY. See CLAUDE.md §5.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
  CREATE ROLE ${APP_DB_USER} WITH LOGIN PASSWORD '${APP_DB_PASSWORD}' NOSUPERUSER;
  GRANT ALL ON DATABASE ${POSTGRES_DB} TO ${APP_DB_USER};
  ALTER SCHEMA public OWNER TO ${APP_DB_USER};
  GRANT ALL ON SCHEMA public TO ${APP_DB_USER};
EOSQL

echo "→ created non-superuser app role '${APP_DB_USER}'"
