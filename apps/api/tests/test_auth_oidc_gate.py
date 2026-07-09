"""OIDC gate consistency (issue #6).

The login page renders its SSO button from ``/meta/modules``' ``oidc_enabled``, linking to
``/api/v1/auth/oidc/login``. The bug: the button was gated on ``oidc_enabled`` alone while
the route also required a discovery URL, so a half-configured instance advertised a login
that 404'd. These tests pin the invariant: the flag is true **iff** the route is mounted.
"""

from __future__ import annotations

import logging

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.routing import NoMatchFound

from app.config import settings
from app.core.auth.router import build_auth_router
from app.core.meta import router as meta_router

_OIDC = {
    "oidc_discovery_url": "https://idp.example.com/.well-known/openid-configuration",
    "oidc_client_id": "client-id",
    "oidc_client_secret": "client-secret",
}


def _apply(monkeypatch: pytest.MonkeyPatch, **overrides) -> None:
    for key, value in overrides.items():
        monkeypatch.setattr(settings, key, value)


def _build_app() -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api/v1")
    api.include_router(build_auth_router())
    api.include_router(meta_router)
    app.include_router(api)
    return app


def _login_route_mounted(app: FastAPI) -> bool:
    # Routers are included lazily on this FastAPI version (app.routes holds unexpanded
    # _IncludedRouter entries), so resolve by route name instead of scanning paths.
    try:
        return app.url_path_for("oidc_login") == "/api/v1/auth/oidc/login"
    except NoMatchFound:
        return False


async def _meta_oidc_enabled(app: FastAPI) -> bool:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://acme.localhost") as c:
        resp = await c.get("/api/v1/meta/modules")
        assert resp.status_code == 200
        return resp.json()["oidc_enabled"]


@pytest.mark.parametrize(
    ("overrides", "expect_mounted"),
    [
        # Fully configured: button and route both present.
        ({"oidc_enabled": True, **_OIDC}, True),
        # The issue #6 misconfiguration: enabled, empty discovery URL (compose default).
        ({"oidc_enabled": True, "oidc_discovery_url": ""}, False),
        # Enabled but credentials missing.
        ({"oidc_enabled": True, "oidc_discovery_url": _OIDC["oidc_discovery_url"]}, False),
        # Disabled: nothing, even with full credentials set.
        ({"oidc_enabled": False, **_OIDC}, False),
    ],
)
async def test_sso_button_iff_route_mounted(
    monkeypatch: pytest.MonkeyPatch, overrides: dict, expect_mounted: bool
) -> None:
    _apply(monkeypatch, **overrides)
    app = _build_app()
    assert _login_route_mounted(app) is expect_mounted
    # The invariant the login page relies on: the flag never diverges from the mount.
    assert await _meta_oidc_enabled(app) is expect_mounted
    if not expect_mounted:
        # The reported symptom: the button's target 404s. When the flag is off it may 404,
        # but the flag must be off — asserted above. (When mounted, GET would redirect to
        # the IdP over the network, so only the unmounted side is exercised by request.)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://acme.localhost"
        ) as c:
            assert (await c.get("/api/v1/auth/oidc/login")).status_code == 404


async def test_enabled_but_unconfigured_warns_naming_missing_settings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _apply(monkeypatch, oidc_enabled=True, oidc_discovery_url="", oidc_client_id=None)
    with caplog.at_level(logging.WARNING, logger="vlotr.auth.oidc"):
        _build_app()
    warning = next(r for r in caplog.records if r.levelno == logging.WARNING)
    assert "VLOTR_OIDC_DISCOVERY_URL" in warning.getMessage()
    assert "VLOTR_OIDC_CLIENT_ID" in warning.getMessage()


async def test_enforced_but_unconfigured_refuses_to_boot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Enforced OIDC disables local login; with no SSO routes either, nobody could sign in.
    _apply(monkeypatch, oidc_enabled=True, oidc_enforced=True, oidc_discovery_url="")
    with pytest.raises(RuntimeError, match="VLOTR_OIDC_DISCOVERY_URL"):
        _build_app()
