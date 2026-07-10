"""FastAPI application factory (CLAUDE.md §4, §6).

Discovers the modules enabled for this deployment, mounts core auth + each module's router
under ``/api/v1``, and installs the standard error envelope. Modules self-register on import.
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.core.auth.router import build_auth_router
from app.core.customfields.router import router as customfields_router
from app.core.dashboard import router as dashboard_router
from app.core.domains import router as domains_router
from app.core.instance.router import router as instance_router
from app.core.members import router as members_router
from app.core.meta import router as meta_router
from app.core.permissions.reconcile import reconcile_permission_defaults
from app.core.permissions.router import permissions_router, roles_router
from app.core.setup import router as setup_router
from app.core.system import readiness
from app.core.system import router as system_router
from app.core.userprefs import router as userprefs_router
from app.errors import register_error_handlers
from app.registry import registry


def _load_enabled_modules() -> None:
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Grant every org's system roles the permissions of any module that shipped after that org
    was seeded (issue #19). One ``SELECT`` per org in steady state, and never fatal — a stale
    catalog is a missing capability, not a reason to refuse to serve."""
    await reconcile_permission_defaults()
    yield


def create_app() -> FastAPI:
    _load_enabled_modules()

    app = FastAPI(
        title="schakl API",
        version=settings.version,
        description="Multi-tenant, modular, white-label agency operations platform.",
        lifespan=lifespan,
    )
    # Needed by the optional OIDC flow (Authlib stores state in the session); harmless otherwise.
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        https_only=settings.auth_cookie_secure,
        same_site="lax",
    )
    register_error_handlers(app)

    api = APIRouter(prefix="/api/v1")
    api.include_router(build_auth_router())
    api.include_router(setup_router)
    api.include_router(meta_router)
    api.include_router(domains_router)
    api.include_router(members_router)
    api.include_router(roles_router)
    api.include_router(permissions_router)
    api.include_router(customfields_router)
    api.include_router(dashboard_router)
    api.include_router(userprefs_router)
    api.include_router(system_router)
    api.include_router(instance_router)
    for module in registry.enabled(settings.enabled_modules):
        if module.router is not None:
            api.include_router(module.router)

    app.include_router(api)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        """Liveness. Must stay cheap, dependency-free and unauthenticated: orchestrators poll
        it, and a probe that touches Postgres would restart a healthy API when the database
        blips. Readiness is a different question — see ``/health/ready``."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["meta"])
    async def health_ready() -> JSONResponse:
        """Readiness: can this instance actually serve? Checks Postgres, Redis and Alembic.

        Unauthenticated (a container healthcheck has no credentials), so the body is
        deliberately detail-free — ``degraded`` never says *which* dependency is down.
        Operators get the breakdown from ``/api/v1/system/info`` or the logs.
        """
        checks = await readiness()
        healthy = all(checks.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ok" if healthy else "degraded"},
        )

    return app


app = create_app()
