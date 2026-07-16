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
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

import app.core.activity.panels  # noqa: F401  — registers the core activity panel on import
from app.config import settings
from app.core.activity.router import router as activity_router
from app.core.ai.router import router as ai_router
from app.core.apikeys.router import router as apikeys_router
from app.core.auth.router import build_auth_router
from app.core.auth.sso_router import router as sso_settings_router
from app.core.customfields.router import router as customfields_router
from app.core.dashboard import router as dashboard_router
from app.core.demo import demo_guard_middleware
from app.core.domains import router as domains_router
from app.core.email.router import router as email_settings_router
from app.core.entitlements.router import router as license_router
from app.core.entitlements.service import AI_SKU, MCP_SKU, LicenseGateASGI, license_write_gate
from app.core.impex.router import build_impex_router
from app.core.instance.router import router as instance_router
from app.core.members import router as members_router
from app.core.meta import router as meta_router
from app.core.nav import router as nav_router
from app.core.permissions.reconcile import reconcile_permission_defaults
from app.core.permissions.router import permissions_router, roles_router
from app.core.providers.router import router as providers_router
from app.core.setup import router as setup_router
from app.core.storage.router import router as files_router
from app.core.system import readiness
from app.core.system import router as system_router
from app.core.userprefs import router as userprefs_router
from app.errors import register_error_handlers
from app.registry import registry


def _load_enabled_modules() -> None:
    for name in settings.enabled_modules:
        importlib.import_module(f"app.modules.{name}")


@asynccontextmanager
async def lifespan(app_: FastAPI) -> AsyncIterator[None]:
    """Grant every org's system roles the permissions of any module that shipped after that org
    was seeded (issue #19). One ``SELECT`` per org in steady state, and never fatal — a stale
    catalog is a missing capability, not a reason to refuse to serve.

    The mounted MCP sub-app (CLAUDE.md §12) brings its own lifespan (the streamable-HTTP
    session manager); FastAPI only runs the outermost one, so it is entered here."""
    await reconcile_permission_defaults()
    if settings.is_cloud and settings.cloud_ingress_dir:
        # Rebuild the custom-domain ingress fragment on boot (#202) — the file lives on a
        # volume that may be fresh; never fatal (sync_ingress logs and swallows).
        from app.core.cloud.ingress import sync_ingress
        from app.db import async_session_maker

        async with async_session_maker() as session:
            await sync_ingress(session)
    mcp_asgi = getattr(app_.state, "mcp_app", None)
    if mcp_asgi is not None:
        async with mcp_asgi.lifespan(mcp_asgi):
            yield
    else:
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
    # Public-demo guardrails (issue #141): a no-op unless SCHAKL_DEMO_MODE is on, then it blocks
    # the catalogued dangerous operations with errors.demo_blocked. Added last so it runs first
    # (Starlette middleware is LIFO), rejecting before routing/auth.
    app.add_middleware(BaseHTTPMiddleware, dispatch=demo_guard_middleware)
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
    api.include_router(activity_router)
    api.include_router(providers_router)
    api.include_router(files_router)
    api.include_router(email_settings_router)
    api.include_router(sso_settings_router)
    api.include_router(dashboard_router)
    api.include_router(nav_router)
    api.include_router(userprefs_router)
    api.include_router(system_router)
    api.include_router(instance_router)
    api.include_router(license_router)
    api.include_router(apikeys_router)
    # The AI core (epic #131) is a licensed surface (issue #137): every generation is a
    # POST, so the standard mutations-gate makes an uncovered instance read-only for AI
    # while its stored settings and usage stay readable.
    api.include_router(ai_router, dependencies=[license_write_gate(AI_SKU)])
    for module in registry.enabled(settings.enabled_modules):
        if module.router is not None:
            # Licensed modules (issue #137) get one mount-time gate: mutations require the
            # sku to be writable (covered by a license, or inside a grace window). Reads
            # never block — an expired module is read-only, not gone.
            deps = [license_write_gate(module.sku)] if module.sku else []
            api.include_router(module.router, dependencies=deps)
    # After module loading on purpose: the impex routes are built per opted-in entity so each
    # one declares that entity's own read/write permission (issue #77, §15 deny-by-default).
    api.include_router(build_impex_router())

    # Cloud posture (epic #199, business-licensed): the tenant's service-access settings, the
    # console's instance additions (PIN claim, plans, instance API keys, /instance/me) and the
    # key-authenticated provisioning API. Mounted unconditionally so the OpenAPI spec (and the
    # generated web client) is posture-independent — but every route carries require_cloud,
    # answering 404 on a self-hosted box. The provisioning surface additionally carries the
    # `cloud` sku's write gate (#137): the business license governs it, with the built-in
    # bootstrap window as the free trial.
    from app.core.cloud.provisioning import router as provisioning_router
    from app.core.cloud.router import instance_router as cloud_instance_router
    from app.core.cloud.router import org_router as cloud_org_router
    from app.core.entitlements.service import CLOUD_SKU

    api.include_router(cloud_org_router)
    api.include_router(cloud_instance_router)
    api.include_router(provisioning_router, dependencies=[license_write_gate(CLOUD_SKU)])

    app.include_router(api)

    # MCP (CLAUDE.md §12): the API surface as tools, API-key authenticated. Built after the
    # routers so the OpenAPI spec it derives from is complete.
    if settings.mcp_enabled:
        from app.core.mcp import build_mcp_asgi_app

        mcp_asgi = build_mcp_asgi_app(app)
        app.state.mcp_app = mcp_asgi
        # The MCP surface is a licensed sku (issue #137). It is read-first by design, so
        # instead of a read/write split the whole surface answers 402 when not covered.
        app.mount("/mcp", LicenseGateASGI(mcp_asgi, sku=MCP_SKU))

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
