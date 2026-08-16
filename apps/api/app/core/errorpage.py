"""The branded error page the API serves when the **web app** is the thing that is down.

An error page cannot live in the service that is down. That is the whole design here, and it is
why this file has a twin in TypeScript rather than a shared implementation: the SSR app renders
the page for an unreachable API (``$lib/core/errors/standalone.server.ts``), and this renders the
page for an unreachable SSR app. Traefik's two ``errors`` middlewares cross-cover — each router's
error page is fetched from the *other* service (``infra/traefik/dynamic.yml``). Sharing code
between them would mean sharing a process, which is exactly the thing that is missing.

Neither is much code, and neither is the surface a design change lands on: the *wording* is the
part that must not drift, and both read it from ``messages/*.json`` through the same keys
(CLAUDE.md §8). The two renderers differ only in markup for the same reason a mail differs from a
screen.

It is deliberately outside ``/api/v1``: it is not part of the product's API, it must not become
an MCP tool or a generated client method (CLAUDE.md §12), and it is addressed by the edge
directly rather than routed to, so nothing publishes it. Reaching it discloses nothing that
``/api/v1/meta/tenant`` does not already serve unauthenticated — it is public branding, which is
the only thing that makes a branded outage page possible at all.
"""

from __future__ import annotations

import html

from fastapi import Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select

from app.config import settings
from app.core.models import OrgSettings
from app.core.tenancy import request_hostname, resolve_org
from app.db import async_session_maker, set_current_org
from app.i18n import translate

# Status → the pair of keys that says what happened. The mirror of `errorCopy` in
# `apps/web/src/lib/core/errors/copy.ts`; keep the two tables in step.
_COPY: dict[int, tuple[str, str]] = {
    401: ("errors.page.unauthorized.title", "errors.page.unauthorized.body"),
    403: ("errors.page.forbidden.title", "errors.page.forbidden.body"),
    404: ("errors.page.not_found.title", "errors.page.not_found.body"),
    408: ("errors.page.unavailable.title", "errors.page.unavailable.body"),
    429: ("errors.page.rate_limited.title", "errors.page.rate_limited.body"),
    502: ("errors.page.unavailable.title", "errors.page.unavailable.body"),
    503: ("errors.page.unavailable.title", "errors.page.unavailable.body"),
    504: ("errors.page.unavailable.title", "errors.page.unavailable.body"),
}

_GENERIC = ("errors.page.generic.title", "errors.page.generic.body")
_SERVER = ("errors.page.server.title", "errors.page.server.body")

# Same neutral fallbacks the web's DEFAULT_THEME carries — never a product brand (Golden Rule 4).
_DEFAULT_PRIMARY = "#4f46e5"

_HEX_DIGITS = set("0123456789abcdefABCDEF")


def _safe_color(value: str | None) -> str:
    """A brand colour is interpolated into a **stylesheet**, where escaping does not help.

    So it is validated as hex and simply not used otherwise. The API only ever stores hex here
    (``PATCH /meta/tenant`` pattern-checks it), which makes this a second lock rather than a
    first one — worth having on the one page that has to render whatever the database holds.
    """
    if not value or not value.startswith("#") or len(value) not in (4, 7):
        return _DEFAULT_PRIMARY
    return value if all(c in _HEX_DIGITS for c in value[1:]) else _DEFAULT_PRIMARY


# Everything except the tenant's own colour, so the document below stays readable and the one
# interpolated value is impossible to lose among a hundred braces. Compact because it ships
# inline on a page that must cost exactly one round-trip.
_LIGHT_VARS = "--bg:#fafafa;--surface:#fff;--text:#171717;--muted:#737373;--border:#e5e5e5"
_RULES = "".join(
    (
        "@media (prefers-color-scheme: dark){:root{"
        "--bg:#0a0a0a;--surface:#171717;--text:#fafafa;--muted:#a3a3a3;--border:#262626}}",
        "*{box-sizing:border-box}",
        "body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
        "padding:1rem;background:var(--bg);color:var(--text);font-family:ui-sans-serif,system-ui,"
        '-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif}',
        "main{width:100%;max-width:26rem;background:var(--surface);"
        "border:1px solid var(--border);border-radius:1rem;padding:2rem;text-align:center;"
        "box-shadow:0 1px 2px rgb(0 0 0 / .05)}",
        ".brand{display:flex;align-items:center;justify-content:center;gap:.5rem;"
        "margin-bottom:1.5rem;font-weight:600;font-size:.95rem}",
        ".brand img{max-height:2rem;max-width:9rem;object-fit:contain}",
        "h1{margin:0 0 .5rem;font-size:1.25rem;line-height:1.4;font-weight:600}",
        "p{margin:0;font-size:.875rem;line-height:1.6;color:var(--muted)}",
        ".code{margin-top:1.25rem;font-size:.75rem;letter-spacing:.05em;"
        "text-transform:uppercase;color:var(--muted)}",
        "a{display:inline-block;margin-top:1.5rem;font-size:.875rem;font-weight:500;"
        "color:var(--brand);text-decoration:underline;text-underline-offset:.2em}",
    )
)


def _copy_for(status: int) -> tuple[str, str]:
    if status in _COPY:
        return _COPY[status]
    if 500 <= status <= 599:
        return _SERVER
    return _GENERIC


class Branding:
    """What the page needs from the tenant, and nothing else."""

    def __init__(
        self,
        *,
        brand_name: str = "",
        logo_url: str | None = None,
        primary_color: str = _DEFAULT_PRIMARY,
        locale: str | None = None,
    ) -> None:
        self.brand_name = brand_name
        self.logo_url = logo_url
        self.primary_color = primary_color
        self.locale = locale


async def _branding_for(request: Request) -> Branding:
    """Resolve the tenant's branding, or fall back to neutral.

    Every failure here is swallowed on purpose. This function runs when something is already
    broken, and the range of "already broken" includes the database: an error page that raises
    while explaining an error is the one outcome worse than an unbranded one.
    """
    try:
        async with async_session_maker() as session:
            org = await resolve_org(session, request_hostname(request))
            if org is None:
                return Branding()
            await set_current_org(session, org.id)
            s = await session.scalar(select(OrgSettings).where(OrgSettings.org_id == org.id))
            if s is None:
                return Branding(brand_name=org.name)
            return Branding(
                brand_name=s.brand_name or org.name,
                logo_url=s.logo_url,
                primary_color=s.primary_color or _DEFAULT_PRIMARY,
                locale=s.default_locale,
            )
    except Exception:  # noqa: BLE001 — see the docstring: never raise from an error page
        return Branding()


def render_error_page(status: int, branding: Branding) -> str:
    """One self-contained document: no stylesheet, no script, no second request.

    The logo is the single remote asset, and it is served by *this* service — so on the outage
    this page exists for (the SSR app is gone, the API is answering) it loads. The brand name
    renders beside it rather than behind it, so a logo that does not arrive costs nothing.
    """
    title_key, body_key = _copy_for(status)
    locale = branding.locale or settings.default_locale
    title = html.escape(translate(title_key, locale))
    body = html.escape(translate(body_key, locale))
    code = html.escape(translate("errors.page.code", locale, status=status))
    action = html.escape(translate("errors.page.home", locale))
    brand = html.escape(branding.brand_name or "")
    primary = _safe_color(branding.primary_color)
    logo = (
        f'<img src="{html.escape(branding.logo_url)}" alt="{brand}">' if branding.logo_url else ""
    )
    mark = f'<div class="brand">{logo or brand}</div>' if (logo or brand) else ""
    style = f":root{{--brand:{primary};{_LIGHT_VARS}}}{_RULES}"

    return (
        "<!doctype html>\n"
        f'<html lang="{html.escape(locale)}">\n'
        "<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        '<meta name="robots" content="noindex">\n'
        f"<title>{title}</title>\n"
        f"<style>{style}</style>\n"
        "</head>\n"
        "<body>\n"
        f"<main>{mark}<h1>{title}</h1><p>{body}</p>"
        f'<a href="/">{action}</a><div class="code">{code}</div></main>\n'
        "</body>\n"
        "</html>\n"
    )


async def error_page(request: Request, status: int) -> HTMLResponse:
    """Handler body for ``GET /error/{status}`` — see :mod:`app.main`."""
    if not (400 <= status <= 599):
        status = 502
    branding = await _branding_for(request)
    return HTMLResponse(
        content=render_error_page(status, branding),
        status_code=status,
        headers={"retry-after": "15", "cache-control": "no-store"},
    )
