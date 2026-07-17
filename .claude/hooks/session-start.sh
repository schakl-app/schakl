#!/bin/bash
#
# SessionStart hook — provisions the web testing harness for Claude Code on the web.
#
# It installs the pnpm workspace dependencies and compiles the Paraglide message
# catalogs, so `pnpm web check|lint|test:unit|test:e2e` all work the moment the
# session opens. Playwright's Chromium is pre-installed in the web image
# (PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers), so we deliberately do NOT run
# `playwright install`.
#
# The e2e suite additionally needs the running stack (api/db/redis/traefik); that
# is a runtime step (`docker compose -f infra/compose.yaml up -d`), not an install
# step, so it is left to the session.
#
# Synchronous by design: the session waits until deps are ready, which prevents a
# race where a test/lint command runs before install finishes.
set -euo pipefail

# Only provision remote (Claude Code on the web) sessions; a local machine already
# has its own working tree and node_modules.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-.}"

echo "[session-start] installing pnpm workspace dependencies…"
# --frozen-lockfile keeps the install deterministic against the committed
# pnpm-lock.yaml; pnpm's content-addressable store makes re-runs fast and this is
# idempotent (safe to run every session).
pnpm install --frozen-lockfile

echo "[session-start] compiling Paraglide message catalogs…"
# Required before the web app can typecheck, build or run e2e (generates
# apps/web/src/lib/paraglide). Idempotent.
pnpm --filter @schakl/web run machine:messages

echo "[session-start] web testing harness ready."
