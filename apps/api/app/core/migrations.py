"""Serialising concurrent ``alembic upgrade`` runs (docs/DEPLOY.md).

THIS IS WHAT LETS THE API RUN MORE THAN ONE REPLICA. The cloud stacks used to pin the API to
``replicas: 1`` + ``order: stop-first`` for one reason: the entrypoint migrates before serving,
and two tasks booting together would race each other through the same revisions. The cost was
that every redeploy had a window with *zero* API tasks — Swarm stops the only task before it
starts the replacement — and the web app, which stays up on ``start-first``, then answered 500 on
every request, because its first server hook fetches ``/meta/tenant`` before anything renders.

The real constraint was never "one replica"; it was "one migration at a time". A Postgres
advisory lock states exactly that and nothing more, so the replica count is free again: whoever
wins the lock migrates, the others wait and then run ``upgrade head`` against an already-current
database, which is a no-op.

It lives here rather than in ``docker-entrypoint.sh`` so that it also covers an operator running
``alembic upgrade head`` by hand in the middle of a rolling deploy — the lock is a property of
migrating, not of one particular way of starting the process.
"""

from __future__ import annotations

import asyncio
import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.config import settings

logger = logging.getLogger(__name__)

# The value is ``zlib.crc32(b"schakl.migrations")`` and MUST NEVER CHANGE. It is a rendezvous
# point between two *different releases* of the app: during a rolling deploy the old and new
# images are both alive, so a release that computed a different key would migrate concurrently
# with the one it is replacing — the exact thing this prevents. Pinned by a test.
MIGRATION_LOCK_KEY = 4018684661

# How often to re-attempt the lock while another instance holds it.
POLL_SECONDS = 2.0


async def acquire_migration_lock(
    connection: AsyncConnection,
    *,
    timeout_seconds: float | None = None,
    poll_seconds: float = POLL_SECONDS,
) -> None:
    """Block until this process owns the migration lock, or raise ``TimeoutError``.

    Polls ``pg_try_advisory_lock`` rather than blocking inside ``pg_advisory_lock`` so the wait is
    bounded and *observable*: a stuck migration would otherwise hang every other replica forever
    with no log line saying why. On timeout we raise, the container fails its healthcheck, and
    Swarm's ``failure_action: rollback`` restores the previous release — which is the outcome you
    want, because the alternative is a task that never serves and never explains itself.

    Session-level (``pg_advisory_lock``), not transaction-level: Alembic owns the migration
    transaction and may commit per revision, and the lock has to outlive those boundaries.
    """
    budget = settings.migration_lock_timeout_seconds if timeout_seconds is None else timeout_seconds
    deadline = time.monotonic() + budget
    waited = False
    while True:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
        if acquired:
            if waited:
                logger.info("acquired the migration lock; continuing")
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"timed out after {budget}s waiting for the migration advisory lock "
                f"({MIGRATION_LOCK_KEY}). Another instance is still migrating, or a previous "
                f"migration died while holding it."
            )
        if not waited:
            waited = True
            logger.info("another instance is migrating; waiting for the migration lock")
        await asyncio.sleep(poll_seconds)


async def release_migration_lock(connection: AsyncConnection) -> None:
    """Best-effort explicit release. Closing the connection would release it anyway — Postgres
    drops session locks when the backend goes away, which is what stops a migration killed
    mid-flight from wedging the next deploy — so a failure here is logged, never raised."""
    try:
        await connection.scalar(
            text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY}
        )
    except Exception:  # noqa: BLE001 — teardown must never mask a migration failure
        logger.warning("could not release the migration lock explicitly", exc_info=True)
