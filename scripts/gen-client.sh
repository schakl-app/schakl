#!/usr/bin/env bash
# Regenerate the typed API client from the API's OpenAPI spec (CLAUDE.md §3).
#
# The web app talks to the API ONLY through this generated client (Golden Rule 6).
# CI runs this and fails if the committed client differs (drift check), so a schema
# change without a regenerated client cannot merge.
#
# Usage:
#   scripts/gen-client.sh                 # export spec from the app, then generate
#   OPENAPI_URL=http://api.localhost/openapi.json scripts/gen-client.sh   # fetch a running API
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API_DIR="$ROOT/apps/api"
WEB_DIR="$ROOT/apps/web"
SPEC_OUT="$WEB_DIR/openapi.json"
CLIENT_OUT="$WEB_DIR/src/lib/core/api/schema.d.ts"

mkdir -p "$(dirname "$CLIENT_OUT")"

if [[ -n "${OPENAPI_URL:-}" ]]; then
  echo "→ fetching OpenAPI from $OPENAPI_URL"
  curl -fsSL "$OPENAPI_URL" -o "$SPEC_OUT"
else
  echo "→ exporting OpenAPI from apps/api (offline)"
  ( cd "$API_DIR" && uv run python -m app.openapi_export ) > "$SPEC_OUT"
fi

echo "→ generating typed client → ${CLIENT_OUT#"$ROOT/"}"
( cd "$WEB_DIR" && pnpm exec openapi-typescript "$SPEC_OUT" -o "$CLIENT_OUT" )

echo "✓ typed client generated."
