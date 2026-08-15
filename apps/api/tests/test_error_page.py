"""The branded error page the edge serves when the **SSR web app** is unreachable.

An error page cannot live in the service that is down, so Traefik's two ``errors`` middlewares
cross-cover: the API's routes take their page from the web app, the web route takes its from here
(``infra/traefik/dynamic.yml``, ``app/core/errorpage.py``).

What these tests pin is what a review cannot see. The page renders in the one situation where
everything else is already broken, so the load-bearing properties are all negative: it must not
raise, must not need a session, must not leak into the product's API surface, and must not
degrade to a stranger's colours on the tenant's own domain.
"""

from __future__ import annotations

import pytest

from app.core.errorpage import Branding, _copy_for, render_error_page
from app.main import app
from tests.conftest import make_tenant


async def test_it_renders_the_tenant_s_own_branding(client_for) -> None:
    """The whole point of serving this from the API instead of a static file.

    A maintenance page in a stranger's colours, on the agency's own domain, reads as "you are on
    the wrong site" at the exact moment a client is already unsure whether something is wrong.
    """
    tenant = await make_tenant("errpage")
    async with client_for(tenant.host) as c:
        res = await c.get("/error/502")

    assert res.status_code == 502
    assert "Errpage" in res.text  # the tenant's brand name, from org_settings
    assert res.headers["content-type"].startswith("text/html")
    # Never cached: an outage page that outlives the outage turns a two-minute rollover into a
    # support ticket. `Retry-After` is what a monitoring probe and a crawler read as "come back".
    assert res.headers["cache-control"] == "no-store"
    assert res.headers["retry-after"] == "15"


async def test_it_speaks_the_tenant_s_language(client_for) -> None:
    """Copy comes from ``messages/*.json`` — the same keys the web's renderer uses, so the two
    pages cannot drift into saying different things about the same outage (CLAUDE.md §8)."""
    tenant = await make_tenant("errnl")
    async with client_for(tenant.host) as c:
        res = await c.get("/error/503")

    assert "Even niet bereikbaar" in res.text
    assert 'lang="nl"' in res.text


async def test_an_unknown_host_still_gets_a_page(client_for) -> None:
    """Resolution failure is not an excuse to render nothing.

    A host that resolves to no org has no branding, which is a reason for a neutral page and
    never for a 500: this endpoint's whole job is to be the thing that answers when other things
    have not (CLAUDE.md §5's strict hostname rule notwithstanding).
    """
    async with client_for("nobody.localhost") as c:
        res = await c.get("/error/502")

    assert res.status_code == 502
    assert "<html" in res.text


async def test_it_needs_no_session(client_for) -> None:
    """Unauthenticated on purpose: during an outage there is no session to read, and everything
    on the page is public branding that ``/meta/tenant`` already serves to a signed-out visitor.
    """
    tenant = await make_tenant("errauth")
    async with client_for(tenant.host) as c:
        res = await c.get("/error/504")
    assert res.status_code == 504


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (404, "Deze pagina bestaat niet"),
        (403, "Je hebt geen toegang"),
        (502, "Even niet bereikbaar"),
        (500, "Er ging iets mis"),
        # Outside the table: an unrecognised 4xx falls back to the generic sentence rather than
        # inventing one, and an unrecognised 5xx is ours.
        (418, "Dit verzoek kon niet verwerkt worden"),
        (599, "Er ging iets mis"),
    ],
)
def test_the_copy_table_covers_the_ranges(status: int, expected: str) -> None:
    html = render_error_page(status, Branding(locale="nl"))
    assert expected in html


def test_a_status_outside_the_table_falls_back_rather_than_inventing() -> None:
    """``{status}`` arrives from the edge's own template, but it is still a URL segment, and a
    stale bookmark or a hand-typed URL can carry anything at all."""
    assert _copy_for(200) == _copy_for(418)  # generic, not a crash and not a success page


def test_tenant_text_is_escaped_into_the_markup() -> None:
    """Brand name and logo URL are free text a tenant types, and this page is one f-string."""
    html = render_error_page(
        502,
        Branding(brand_name="<script>alert(1)</script>", logo_url='"><img onerror=x>'),
    )
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
    assert "onerror=x>" not in html


def test_a_colour_that_is_not_a_colour_never_reaches_the_stylesheet() -> None:
    """The brand colour is interpolated into CSS, where escaping does not help — so it is
    validated as hex and simply not used otherwise. The API stores hex; a row that holds
    anything else got there some other way, and this is the page that must not care."""
    html = render_error_page(502, Branding(primary_color="red;} body{display:none"))
    assert "display:none" not in html
    assert "--brand:#4f46e5" in html


def test_it_is_not_part_of_the_product_api() -> None:
    """Out of the OpenAPI document on purpose.

    Every ``/api/v1`` operation becomes an MCP tool and a method on the generated client
    (CLAUDE.md §12). An HTML error page is neither, and it lives outside ``/api/v1`` for the same
    reason ``/health`` does — which is also what keeps it out of the deny-by-default sweep, whose
    subject is the product's authorized surface.
    """
    assert "/error/{status}" not in app.openapi()["paths"]
    # Asserted through the router rather than by scanning ``app.routes``: included routers are
    # resolved lazily, so a path scan answers about the wrong object.
    assert app.url_path_for("edge_error_page", status=502) == "/error/502"
