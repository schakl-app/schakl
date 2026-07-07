"""FastAPI application factory (CLAUDE.md §4, §6).

Discovers the modules enabled for this deployment, mounts core auth + each module's router
under ``/api/v1``, and installs the standard error envelope. Modules self-register on import.
"""

from __future__ import annotations

import importlib

from fastapi import APIRouter, FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app.config import settings
from app.core.auth.router import build_auth_router
from app.core.customfields.router import router as customfields_router
from app.core.members import router as members_router
from app.core.meta import router as meta_router
from app.errors import register_error_handlers
from app.registry import registry


def _load_enabled_modules() -> None:
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")


def create_app() -> FastAPI:
    _load_enabled_modules()

    app = FastAPI(
        title="vlotr API",
        version="0.0.0",
        description="Multi-tenant, modular, white-label agency operations platform.",
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
    api.include_router(meta_router)
    api.include_router(members_router)
    api.include_router(customfields_router)
    for module in registry.enabled(settings.enabled_modules):
        if module.router is not None:
            api.include_router(module.router)

    app.include_router(api)

    @app.get("/health", tags=["meta"])
    async def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
