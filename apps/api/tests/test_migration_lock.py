"""The migration advisory lock, and the deploy shape it unlocks (docs/DEPLOY.md).

What is being pinned here is a *deployment* property, and it is invisible in every functional
test: the API used to run one replica with `order: stop-first`, because the entrypoint migrates
before serving and two tasks would race `alembic upgrade head`. That made every cloud redeploy a
guaranteed API outage, which the web app turned into a 500 on every request. Moving the mutual
exclusion into a Postgres advisory lock is what lets the API run two replicas and roll one at a
time — so both halves are asserted: the lock genuinely excludes, and the stacks genuinely use
start-first with more than one replica.
"""

from __future__ import annotations

import asyncio
import zlib
from pathlib import Path

import pytest
import yaml
from sqlalchemy import text

from app.core.migrations import (
    MIGRATION_LOCK_KEY,
    acquire_migration_lock,
    release_migration_lock,
)
from app.db import engine

INFRA = Path(__file__).resolve().parents[3] / "infra"
SWARM_STACKS = ("compose.portainer.yml", "compose.swarm.yml")


async def _lock_conn():
    """An AUTOCOMMIT connection — its own Postgres backend, so it holds its own session locks."""
    conn = await engine.connect()
    await conn.execution_options(isolation_level="AUTOCOMMIT")
    return conn


def test_lock_key_is_the_pinned_rendezvous_value() -> None:
    """The key is a rendezvous point between two *different releases* of the app: during a
    rolling deploy the old and new images are both alive. A release that computed a different
    key would migrate concurrently with the one it is replacing — the exact race this
    prevents — and every functional test would still pass. Hence a literal, and this guard."""
    assert MIGRATION_LOCK_KEY == zlib.crc32(b"schakl.migrations")
    assert MIGRATION_LOCK_KEY == 4018684661


async def test_lock_excludes_a_second_holder() -> None:
    """The load-bearing claim: while one instance migrates, another cannot start."""
    first = await _lock_conn()
    second = await _lock_conn()
    try:
        await acquire_migration_lock(first, timeout_seconds=5)

        with pytest.raises(TimeoutError, match="migration advisory lock"):
            await acquire_migration_lock(second, timeout_seconds=0.2, poll_seconds=0.05)
    finally:
        await release_migration_lock(first)
        await first.close()
        await second.close()


async def test_waiter_proceeds_once_the_holder_releases() -> None:
    """A booting replica must not fail because another one was mid-migration — it waits, then
    runs `upgrade head` against an already-current database and no-ops."""
    holder = await _lock_conn()
    waiter = await _lock_conn()
    try:
        await acquire_migration_lock(holder, timeout_seconds=5)

        async def _release_shortly() -> None:
            await asyncio.sleep(0.15)
            await release_migration_lock(holder)

        releaser = asyncio.create_task(_release_shortly())
        # Would raise TimeoutError if the release were not observed.
        await acquire_migration_lock(waiter, timeout_seconds=5, poll_seconds=0.05)
        await releaser
    finally:
        await release_migration_lock(waiter)
        await holder.close()
        await waiter.close()


async def test_a_dead_holder_does_not_wedge_the_next_deploy() -> None:
    """A migration killed mid-flight (a forced `service update`, an OOM) must not leave the lock
    held forever — otherwise one bad deploy would block every subsequent one. Postgres drops
    session locks when the backend goes away, which is *why* the lock rides its own connection."""
    doomed = await _lock_conn()
    await acquire_migration_lock(doomed, timeout_seconds=5)
    await doomed.close()  # no release — simulates the process dying

    survivor = await _lock_conn()
    try:
        await acquire_migration_lock(survivor, timeout_seconds=5, poll_seconds=0.05)
    finally:
        await release_migration_lock(survivor)
        await survivor.close()


async def test_lock_is_actually_released() -> None:
    """`release_migration_lock` must really drop it, not just return quietly — otherwise the
    'dead holder' path above would be the only thing that ever freed it."""
    conn = await _lock_conn()
    try:
        await acquire_migration_lock(conn, timeout_seconds=5)
        await release_migration_lock(conn)
        held = await conn.scalar(
            text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objid = :k"),
            {"k": MIGRATION_LOCK_KEY % (2**32)},
        )
        assert held == 0
    finally:
        await conn.close()


@pytest.mark.parametrize("stack", SWARM_STACKS)
def test_api_rolls_without_an_outage(stack: str) -> None:
    """`replicas: 1` + `stop-first` is a guaranteed window with zero API tasks. Both halves have
    to stay fixed together: two replicas with stop-first still drains to a gap under parallelism,
    and start-first with one replica is a contradiction Swarm resolves by stopping first."""
    deploy = yaml.safe_load((INFRA / stack).read_text())["services"]["api"]["deploy"]

    assert deploy["update_config"]["order"] == "start-first", (
        f"{stack}: api must roll start-first, or every redeploy is an API outage"
    )
    # Written as `${API_REPLICAS:-2}`; the default is what ships.
    assert "-2}" in str(deploy["replicas"]) or int(deploy["replicas"]) >= 2, (
        f"{stack}: api must default to >= 2 replicas for start-first to mean anything"
    )
    assert deploy["update_config"]["failure_action"] == "rollback"
    # The default monitor is 5s — shorter than the boot, so rollback would never fire.
    assert deploy["update_config"]["monitor"] not in (None, "5s")


@pytest.mark.parametrize("stack", SWARM_STACKS)
def test_web_declares_readiness(stack: str) -> None:
    """Without a healthcheck, `start-first` rotates a new web task into Traefik the moment the
    container is *running* — before the SSR server binds :3000. The probe must also not call the
    API, or an API restart would pull every web replica out of rotation at once."""
    web = yaml.safe_load((INFRA / stack).read_text())["services"]["web"]

    probe = " ".join(web["healthcheck"]["test"])
    assert "/healthz" in probe, f"{stack}: web healthcheck must hit the dependency-free /healthz"
    assert "meta/tenant" not in probe and "api:8000" not in probe, (
        f"{stack}: web readiness must not depend on the API being up"
    )
