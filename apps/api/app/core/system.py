"""Instance health, version and diagnostics (issue #18).

Three surfaces, deliberately kept apart because they have different callers and different
threat models:

``GET /health``
    Liveness, for orchestrators. Cheap, unauthenticated, touches nothing. Lives in ``main.py``.

``GET /health/ready``
    Readiness. Checks Postgres, Redis and whether Alembic sits at head. Unauthenticated —
    Docker/Compose healthchecks have no credentials — so it answers ``ok``/``degraded`` and
    nothing else. *Which* dependency is down is not a stranger's business.

``GET /api/v1/system/info``
    The detailed diagnostics behind the Settings → System screen. Authenticated and
    **manager-gated**: exact versions and dependency topology are reconnaissance, and a
    self-hosted box sits on the public internet behind a Cloudflare Tunnel.

Everything here describes the **installation**, not a tenant. It is org-agnostic on purpose:
no ``org_id`` appears in a response, and the tenant context is used only to authenticate the
caller and check their role.
"""

from __future__ import annotations

import logging
import platform
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from arq.constants import default_queue_name
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.cache import WORKER_HEARTBEAT_KEY, get_redis
from app.core.tenancy import RequestContext, require_context
from app.core.update_check import cached_update_status
from app.db import async_session_maker
from app.registry import registry

logger = logging.getLogger("vlotr.system")

router = APIRouter(prefix="/system", tags=["system"])

# apps/api/ — holds alembic.ini and the alembic/ script directory.
_API_DIR = Path(__file__).resolve().parents[2]

_OK = "ok"
_DOWN = "down"


# --------------------------------------------------------------------------- #
# Migrations
# --------------------------------------------------------------------------- #
@lru_cache(maxsize=1)
def alembic_heads() -> tuple[str, ...]:
    """Revision(s) this *image* ships. Static per build, so read the script tree once.

    More than one head means someone merged two migration branches without a merge revision —
    worth surfacing, since ``alembic upgrade head`` would refuse to run.
    """
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(str(_API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(_API_DIR / "alembic"))
    return tuple(ScriptDirectory.from_config(cfg).get_heads())


async def current_revisions(session: AsyncSession) -> tuple[str, ...]:
    """Revision(s) the *database* is at. Empty when the schema was never migrated."""
    try:
        rows = await session.execute(text("SELECT version_num FROM alembic_version"))
    except Exception:
        # Table absent (fresh database) or unreadable — either way, not at head.
        return ()
    return tuple(sorted(r[0] for r in rows))


async def migration_status(session: AsyncSession) -> dict:
    heads = alembic_heads()
    current = await current_revisions(session)
    return {
        "current": list(current),
        "head": list(heads),
        # A populated database that is behind head is the classic self-host failure: the
        # entrypoint runs `alembic upgrade head` before uvicorn binds, so this being false on
        # a *running* API means the migration was skipped, not that it is still in flight.
        "up_to_date": bool(current) and set(current) == set(heads),
    }


# --------------------------------------------------------------------------- #
# Dependency probes — each returns (status, detail) and never raises
# --------------------------------------------------------------------------- #
async def probe_database(session: AsyncSession) -> dict:
    try:
        await session.execute(text("SELECT 1"))
        version = await session.scalar(text("SHOW server_version"))
    except Exception:
        logger.warning("database probe failed", exc_info=True)
        return {"status": _DOWN, "version": None}
    return {"status": _OK, "version": str(version).split(" ", 1)[0] if version else None}


async def probe_redis() -> dict:
    try:
        info = await get_redis().info("server")
    except Exception:
        logger.warning("redis probe failed", exc_info=True)
        return {"status": _DOWN, "version": None}
    return {"status": _OK, "version": info.get("redis_version")}


async def probe_worker() -> dict:
    """Worker liveness from the heartbeat the worker's own cron writes every minute.

    A dead ARQ worker is otherwise completely invisible: the API keeps serving, jobs simply
    pile up. ``queue_depth`` reads arq's queue (a sorted set), which is absent when empty.
    """
    result: dict = {"status": _DOWN, "last_seen_at": None, "queue_depth": None}
    try:
        redis = get_redis()
        last_seen = await redis.get(WORKER_HEARTBEAT_KEY)
        result["queue_depth"] = int(await redis.zcard(default_queue_name))
    except Exception:
        logger.warning("worker probe failed", exc_info=True)
        return result

    if last_seen:
        # The key carries a TTL, so its mere presence means a heartbeat landed recently.
        result["status"] = _OK
        result["last_seen_at"] = last_seen
    return result


async def readiness() -> dict[str, bool]:
    """The three checks behind ``/health/ready``. Opens its own session — no tenant involved."""
    async with async_session_maker() as session:
        database = await probe_database(session)
        migrations = await migration_status(session)
    redis = await probe_redis()
    return {
        "database": database["status"] == _OK,
        "redis": redis["status"] == _OK,
        "migrations": migrations["up_to_date"],
    }


# --------------------------------------------------------------------------- #
# GET /api/v1/system/info
# --------------------------------------------------------------------------- #
class BuildInfo(BaseModel):
    version: str
    git_sha: str
    built_at: str | None
    environment: str
    python_version: str


class DependencyInfo(BaseModel):
    status: str
    version: str | None = None


class WorkerInfo(BaseModel):
    status: str
    last_seen_at: str | None = None
    queue_depth: int | None = None


class MigrationInfo(BaseModel):
    current: list[str]
    head: list[str]
    up_to_date: bool


class UpdateInfo(BaseModel):
    enabled: bool
    current: str
    latest: str | None = None
    release_url: str | None = None
    checked_at: str | None = None
    update_available: bool


class SystemInfo(BaseModel):
    build: BuildInfo
    database: DependencyInfo
    redis: DependencyInfo
    worker: WorkerInfo
    migrations: MigrationInfo
    update: UpdateInfo
    enabled_modules: list[str]
    server_time: str


@router.get("/info", response_model=SystemInfo)
async def system_info(ctx: RequestContext = Depends(require_context)) -> SystemInfo:
    """Diagnostics for the Settings → System screen. Owners and admins only."""
    ctx.ensure_can_manage()

    database = await probe_database(ctx.session)
    migrations = await migration_status(ctx.session)
    redis = await probe_redis()
    worker = await probe_worker()
    update = await cached_update_status()

    return SystemInfo(
        build=BuildInfo(
            version=settings.version,
            git_sha=settings.git_sha,
            built_at=settings.built_at,
            environment=settings.environment,
            python_version=platform.python_version(),
        ),
        database=DependencyInfo(**database),
        redis=DependencyInfo(**redis),
        worker=WorkerInfo(**worker),
        migrations=MigrationInfo(**migrations),
        update=UpdateInfo(**update),
        enabled_modules=[m.name for m in registry.enabled(settings.enabled_modules)],
        server_time=datetime.now(UTC).isoformat(),
    )


__all__ = ["alembic_heads", "readiness", "router"]
