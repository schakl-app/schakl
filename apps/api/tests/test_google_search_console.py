"""The Google Search Console surface: what it answers, what it refuses, and where it lives.

Four groups, and each one is a rule the code would otherwise only look like it follows.

1. **The credential's absence is evidence, never a verdict** (#411). Listing sites with no
   Google connection is a *state* with a cure, so it answers; naming a site is a refusal, and
   the three ways it can fail are three different sentences for three different people.
2. **A read stays a read.** Every route is a GET and every route declares a permission, so the
   licence gate cannot make Search Console stop answering and deny-by-default stays enumerable.
3. **The section is derived, not written down.** ``/mcp/google-search-console`` is this module's
   own router prefix — and it shares a first segment with ``/google``, ``/google-ads`` and
   ``/google-analytics``, none of which may swallow it.
4. **AI visibility is a state, not a number.** The Search Analytics API has no search type for
   the console's Generative AI report (checked against Google's discovery document), so the
   answer says ``available: false`` and hands over the report — and the marketing dashboard's
   card says the same thing from the same function.
"""

from __future__ import annotations

import pytest

from app.core.ai.tools import available_tools
from app.core.crypto import encrypt
from app.core.permissions import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.integrations.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_SEARCH_CONSOLE
from app.integrations.google_search_console import client as gsc_client
from app.integrations.google_search_console.client import (
    GENERATIVE_AI_SEARCH_TYPES,
    SEARCH_TYPES,
    generative_ai_report_url,
    set_transport,
    site_url,
)
from app.integrations.google_search_console.mcp import GOOGLE_SEARCH_CONSOLE_MCP_TOOLS
from app.integrations.google_search_console.service import (
    AI_NOT_IN_API,
    GoogleSearchConsoleService,
    parse_filters,
)
from tests.conftest import auth_cookie, make_tenant
from tests.gsc_fake import PREFIX_SITE, SITE, FakeSearchConsole, row

pytestmark = pytest.mark.asyncio

_READ = PermissionSet.of(["google_search_console.site.read"])
_RUN = PermissionSet.of(
    ["google_search_console.site.read", "google_search_console.report.run"]
)


@pytest.fixture
def fake() -> FakeSearchConsole:
    stub = FakeSearchConsole()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


async def _connected(
    slug: str,
    *,
    scopes: tuple[str, ...] = (SCOPE_SEARCH_CONSOLE,),
    status: str | None = None,
):
    """An org whose owner holds a Google grant carrying ``scopes``."""
    t = await make_tenant(slug)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        session.add(
            GoogleSettings(
                org_id=t.org.id,
                client_id="fake-client-id",
                client_secret_encrypted=encrypt("fake-client-secret"),
            )
        )
        session.add(
            GoogleConnection(
                org_id=t.org.id,
                user_id=t.user.id,
                google_sub="sub-1",
                email="gsc@example.com",
                scopes=list(scopes),
                refresh_token_encrypted=encrypt("1//fake-refresh-token"),
                status=status or ConnectionStatus.ACTIVE.value,
            )
        )
        await session.commit()
    return t


async def _ctx(t, permissions: PermissionSet = _READ):
    session = async_session_maker()
    await session.__aenter__()
    await set_current_org(session, t.org.id)
    return session, RequestContext(
        user=t.user, org=t.org, session=session, permissions=permissions
    )


# --- the vocabulary ------------------------------------------------------------------------- #
async def test_a_bare_hostname_is_read_as_the_domain_property() -> None:
    """A model types ``klant.nl``; Google wants ``sc-domain:klant.nl``; a URL-prefix property
    is already a URL and is left alone."""
    assert site_url("klant.nl") == "sc-domain:klant.nl"
    assert site_url(" sc-domain:klant.nl ") == "sc-domain:klant.nl"
    assert site_url("https://www.klant.nl/") == "https://www.klant.nl/"


async def test_the_generative_ai_report_is_not_in_the_api_yet_and_the_seam_says_so() -> None:
    """The tuple is empty on purpose, and the report URL is one function for every surface.

    A test that will fail the day it is populated: the *other* assertions here — the state and
    the dashboard card — then need revisiting, which is the point of pinning it.
    """
    assert GENERATIVE_AI_SEARCH_TYPES == ()
    assert set(SEARCH_TYPES) == {"web", "image", "video", "news", "discover", "googleNews"}
    assert gsc_client.API_REVISION_CHECKED >= "20260902"
    assert generative_ai_report_url(SITE) == (
        "https://search.google.com/search-console/performance/search-analytics/ai"
        "?resource_id=sc-domain%3Aklant.nl"
    )


# --- the credential ---------------------------------------------------------------------- #
async def test_no_connection_is_a_state_the_listing_reports_rather_than_an_error() -> None:
    t = await make_tenant("gsc-unconnected")
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleSearchConsoleService(ctx).sites()
    finally:
        await session.__aexit__(None, None, None)
    assert listing.connected is False
    assert listing.has_scope is False
    assert listing.sites == []


async def test_a_connection_without_the_scope_says_so_and_asks_google_nothing(fake) -> None:
    t = await _connected("gsc-noscope", scopes=())
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleSearchConsoleService(ctx).sites()
        with pytest.raises(AppError) as refusal:
            await GoogleSearchConsoleService(ctx).overview(SITE)
        with pytest.raises(AppError) as ai_refusal:
            await GoogleSearchConsoleService(ctx).ai_visibility(SITE)
    finally:
        await session.__aexit__(None, None, None)
    assert listing.connected is True and listing.has_scope is False
    assert refusal.value.message_key == "errors.google_search_console_scope_missing"
    assert refusal.value.status_code == 409
    # The AI-visibility answer is a state with a link, but the link lands in *this* account,
    # so a caller who is not connected learns that rather than being handed the link.
    assert ai_refusal.value.message_key == "errors.google_search_console_scope_missing"
    assert fake.requests == []


# --- what it answers --------------------------------------------------------------------- #
async def test_sites_are_listed_with_their_kind_and_permission(fake) -> None:
    t = await _connected("gsc-sites")
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleSearchConsoleService(ctx).sites()
        detail = await GoogleSearchConsoleService(ctx).site_detail("klant.nl")
    finally:
        await session.__aexit__(None, None, None)
    # Sorted on what a list prints, so a domain property and a URL-prefix one interleave.
    assert [s.site_url for s in listing.sites] == ["sc-domain:ander.nl", PREFIX_SITE, SITE]
    by_url = {s.site_url: s for s in listing.sites}
    assert by_url[SITE].display_name == "klant.nl"
    assert by_url[SITE].site_type == "domain"
    assert by_url[SITE].permission_level == "siteOwner"
    assert by_url[PREFIX_SITE].site_type == "url_prefix"
    assert detail.site_url == SITE
    # The bare hostname reached Google as the domain property, encoded whole.
    assert fake.requests[-1][0].endswith("/webmasters/v3/sites/sc-domain:klant.nl")


async def test_sitemaps_carry_googles_counts_as_numbers(fake) -> None:
    """Google sends ``errors`` and ``warnings`` as strings; a reader compares them as numbers."""
    t = await _connected("gsc-sitemaps")
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleSearchConsoleService(ctx).sitemaps(SITE)
        one = await GoogleSearchConsoleService(ctx).sitemap(
            SITE, "https://www.klant.nl/sitemap.xml"
        )
    finally:
        await session.__aexit__(None, None, None)
    assert len(listing.sitemaps) == 1
    assert listing.sitemaps[0].warnings == 2 and listing.sitemaps[0].errors == 0
    assert listing.sitemaps[0].contents == [{"type": "web", "submitted": 143}]
    assert one.path == "https://www.klant.nl/sitemap.xml"
    # The sitemap URL is a path segment and every character of it is encoded.
    assert fake.requests[-1][0].endswith(
        "/sitemaps/https://www.klant.nl/sitemap.xml"
    )


async def test_the_overview_is_three_queries_in_flight_and_names_what_it_compared(fake) -> None:
    """The compared span is *stated*: "up 20 %" over an unnamed period is not a claim anybody
    can check (#312). And position is the metric where down is good, which the change says."""

    def analytics(body):
        if body.get("dimensions") == ["device"]:
            return {"rows": [row(["MOBILE"], 90, 3000, 9.1), row(["DESKTOP"], 30, 1000, 6.2)]}
        if body["startDate"].startswith("2025"):
            return {"rows": [row(None, 100, 5000, 9.0)]}
        return {
            "rows": [row(None, 120, 4000, 8.4)],
            "metadata": {"firstIncompleteDate": body["endDate"]},
        }

    fake.analytics = analytics
    t = await _connected("gsc-overview")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleSearchConsoleService(ctx).overview(SITE, period="30d")
    finally:
        await session.__aexit__(None, None, None)
    assert len(fake.requests) == 3
    bodies = [body for _, body in fake.requests]
    assert all(body["type"] == "web" and body["dataState"] == "all" for body in bodies)
    assert result.period.days == 30
    assert result.compared_with.mode == "year"
    assert result.totals["clicks"] == 120 and result.previous_totals["clicks"] == 100
    assert result.change["clicks"].relative == 0.2
    assert result.change["position"].absolute == -0.6
    assert result.change["position"].lower_is_better is True
    assert result.devices["MOBILE"]["clicks"] == 90
    assert result.fresh_from == result.period.date_to
    assert "google_search_console.warning.fresh_data" in result.warnings


async def test_a_delta_against_no_baseline_is_undefined_rather_than_infinite(fake) -> None:
    def analytics(body):
        if body.get("dimensions"):
            return {"rows": []}
        if body["startDate"].startswith("2025"):
            return {"rows": []}
        return {"rows": [row(None, 10, 100, 4.0)]}

    fake.analytics = analytics
    t = await _connected("gsc-nobaseline")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleSearchConsoleService(ctx).overview(SITE)
    finally:
        await session.__aexit__(None, None, None)
    assert result.previous_totals["clicks"] == 0
    assert result.change["clicks"].relative is None
    assert result.change["clicks"].absolute == 10


async def test_a_page_of_a_longer_table_says_that_it_is_one(fake) -> None:
    """Google reports no total, so a full page is learned by asking for one row more (§17)."""

    def analytics(body):
        assert body["rowLimit"] == 3, "limit + 1, so a full page can say it is not the whole"
        return {
            "rows": [
                row(["fiets kopen"], 40, 900, 3.2),
                row(["e-bike"], 12, 500, 7.7),
                row(["racefiets"], 5, 300, 11.0),
            ]
        }

    fake.analytics = analytics
    t = await _connected("gsc-truncated")
    session, ctx = await _ctx(t)
    try:
        table = await GoogleSearchConsoleService(ctx).breakdown(SITE, dimension="query", limit=2)
    finally:
        await session.__aexit__(None, None, None)
    assert table.dimensions == ["query"]
    assert len(table.rows) == 2 and table.truncated is True
    assert table.rows[0].dimensions == {"query": "fiets kopen"}
    assert table.rows[0].metrics["clicks"] == 40.0
    assert 0 < table.rows[0].metrics["ctr"] < 1


async def test_another_order_is_applied_within_a_window_and_says_so(fake) -> None:
    """Google ranks by clicks and offers nothing else; a top-N by impressions out of the first
    thousand by clicks is not the same list, and the answer has to say which one it is."""
    fake.analytics = lambda body: {
        "rows": [row(["a"], 40, 900, 3.2), row(["b"], 12, 5000, 7.7), row(["c"], 5, 300, 11.0)]
    }
    t = await _connected("gsc-order")
    session, ctx = await _ctx(t)
    try:
        table = await GoogleSearchConsoleService(ctx).breakdown(
            SITE, dimension="query", limit=2, order="-impressions"
        )
    finally:
        await session.__aexit__(None, None, None)
    assert fake.requests[-1][1]["rowLimit"] == gsc_client.ORDER_WINDOW + 1
    assert [r.dimensions["query"] for r in table.rows] == ["b", "a"]
    assert table.truncated is True
    assert "google_search_console.warning.order_window" in table.warnings


async def test_the_hourly_read_asks_for_hourly_all_today_included(fake) -> None:
    """The hour dimension answers nothing under any other data state, and the one read whose
    window ends today rather than yesterday is the one that exists for "this morning"."""
    fake.analytics = lambda body: {
        "rows": [row(["2026-09-04T09:00:00-07:00"], 3, 40, 5.0)],
        "metadata": {"firstIncompleteHour": "2026-09-04T09:00:00-07:00"},
    }
    t = await _connected("gsc-hourly")
    session, ctx = await _ctx(t)
    try:
        table = await GoogleSearchConsoleService(ctx).hourly(SITE, days=3)
    finally:
        await session.__aexit__(None, None, None)
    body = fake.requests[-1][1]
    assert body["dimensions"] == ["hour"] and body["dataState"] == "hourly_all"
    assert body["endDate"] == table.period.date_to.isoformat()
    assert table.period.days == 3
    assert table.data_state == "hourly_all"
    assert "google_search_console.warning.fresh_hours" in table.warnings


async def test_movers_normalise_the_sign_so_that_positive_is_a_climb(fake) -> None:
    def analytics(body):
        if body["startDate"].startswith("2025"):
            return {"rows": [row(["a"], 10, 300, 12.0), row(["gone"], 4, 100, 20.0)]}
        return {
            "rows": [
                row(["a"], 30, 400, 4.0),
                row(["new"], 2, 50, 30.0),
                row(["tiny"], 0, 3, 50.0),
            ]
        }

    fake.analytics = analytics
    t = await _connected("gsc-movers")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleSearchConsoleService(ctx).movers(SITE, period="30d")
    finally:
        await session.__aexit__(None, None, None)
    assert [r.label for r in result.rows] == ["a"]
    assert result.rows[0].change == 8.0 and result.rows[0].position == 4.0
    # "tiny" fell under the impressions floor and is not counted as having entered.
    assert result.entered == 1 and result.dropped == 1


async def test_the_free_form_query_refuses_an_unknown_dimension_before_asking_google(fake) -> None:
    """Google's own 400 names neither the bad value nor the good ones; this module knows the
    list, so it says it (§9's machine-readable ``details``)."""
    t = await _connected("gsc-badquery")
    session, ctx = await _ctx(t)
    try:
        with pytest.raises(AppError) as refusal:
            await GoogleSearchConsoleService(ctx).query(SITE, dimensions=["query", "keyword"])
        with pytest.raises(AppError) as refusal_type:
            await GoogleSearchConsoleService(ctx).query(
                SITE, dimensions=["query"], search_type="ai"
            )
        answered = await GoogleSearchConsoleService(ctx).query(
            SITE,
            dimensions=["Query", "PAGE", "hour"],
            search_type="Discover",
            filters=["query=@fiets"],
            aggregation="byPage",
            limit=5,
        )
    finally:
        await session.__aexit__(None, None, None)
    assert refusal.value.status_code == 422
    assert refusal.value.details["allowed"] == list(gsc_client.DIMENSIONS)
    assert refusal_type.value.details["allowed"] == list(SEARCH_TYPES)
    body = fake.requests[-1][1]
    # Case-folded to Google's spelling, the hour dimension forced its data state, and the
    # filter travelled in Google's own shape.
    assert body["dimensions"] == ["query", "page", "hour"]
    assert body["type"] == "discover" and body["dataState"] == "hourly_all"
    assert body["aggregationType"] == "byPage"
    assert body["dimensionFilterGroups"] == [
        {
            "groupType": "and",
            "filters": [{"dimension": "query", "operator": "contains", "expression": "fiets"}],
        }
    ]
    assert answered.search_type == "discover"


async def test_the_inspection_flattens_the_verdict_and_keeps_googles_detail(fake) -> None:
    t = await _connected("gsc-inspect")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleSearchConsoleService(ctx).inspect(
            SITE, url="https://www.klant.nl/fietsen/", language="nl"
        )
    finally:
        await session.__aexit__(None, None, None)
    path, body = fake.requests[-1]
    assert path.endswith("/v1/urlInspection/index:inspect")
    assert body == {
        "siteUrl": SITE,
        "inspectionUrl": "https://www.klant.nl/fietsen/",
        "languageCode": "nl",
    }
    assert result.verdict == "PASS"
    assert result.coverage_state == "Submitted and indexed"
    assert result.google_canonical == result.user_canonical
    assert result.rich_results["verdict"] == "PASS"
    assert result.inspection_link.startswith("https://search.google.com/")


async def test_ai_visibility_is_a_state_with_the_report_and_never_a_guessed_number(fake) -> None:
    """The answer a model would otherwise invent from the web totals."""
    t = await _connected("gsc-ai")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleSearchConsoleService(ctx).ai_visibility(SITE, period="last_month")
    finally:
        await session.__aexit__(None, None, None)
    assert result.available is False
    assert result.reason == AI_NOT_IN_API
    assert result.report_url == generative_ai_report_url(SITE)
    assert result.sources == {}
    assert result.features == ["AI Overviews", "AI Mode"]
    # Nothing was asked of Google: there is no query that could answer this today.
    assert fake.requests == []


async def test_googles_refusal_becomes_a_reason_code_and_never_its_own_prose(fake) -> None:
    """Untranslated vendor English in the envelope is a screen in the wrong language (§9)."""
    fake.failures["/searchAnalytics/query"] = (400, "badRequest")
    t = await _connected("gsc-badrequest")
    session, ctx = await _ctx(t)
    try:
        with pytest.raises(AppError) as refusal:
            await GoogleSearchConsoleService(ctx).timeseries(SITE)
        fake.failures["/searchAnalytics/query"] = (403, "forbidden")
        with pytest.raises(AppError) as denied:
            await GoogleSearchConsoleService(ctx).timeseries(SITE)
        fake.failures["/searchAnalytics/query"] = (429, "rateLimitExceeded")
        with pytest.raises(AppError) as limited:
            await GoogleSearchConsoleService(ctx).timeseries(SITE)
    finally:
        await session.__aexit__(None, None, None)
    assert refusal.value.status_code == 422
    assert refusal.value.message_key == "errors.google_search_console_invalid_request"
    assert refusal.value.details["google_reason"] == "badRequest"
    assert "nope" not in str(refusal.value.details.values())
    assert denied.value.status_code == 403
    assert limited.value.status_code == 429
    assert limited.value.message_key == "errors.google_search_console_quota"


# --- the filter grammar ------------------------------------------------------------------ #
async def test_a_filter_clause_is_refused_rather_than_ignored() -> None:
    """A filter silently dropped answers a different question with every row valid — the
    SnelStart ``$filter`` failure in a query string. So is a filter on a dimension Google
    cannot filter on."""
    assert parse_filters([]) == []
    assert parse_filters(["query=@fiets", "country==nld", "page!~/blog/"]) == [
        {"dimension": "query", "operator": "contains", "expression": "fiets"},
        {"dimension": "country", "operator": "equals", "expression": "nld"},
        {"dimension": "page", "operator": "excludingRegex", "expression": "/blog/"},
    ]
    assert parse_filters(["searchappearance!=VIDEO"])[0]["dimension"] == "searchAppearance"
    with pytest.raises(AppError) as unknown_operator:
        parse_filters(["query~fiets"])
    assert unknown_operator.value.status_code == 422
    with pytest.raises(AppError) as unfilterable:
        parse_filters(["date==2026-07-01"])
    assert unfilterable.value.details["allowed"] == list(gsc_client.FILTER_DIMENSIONS)


# --- the routes ---------------------------------------------------------------------------- #
async def test_every_route_is_a_get_that_declares_a_permission() -> None:
    """All-GET is the design, not a phase: nothing here is worth writing, and a read must keep
    answering past a licence expiry, which the write gate decides by *method* (§18)."""
    from app.main import app

    spec = app.openapi()
    paths = {
        p: ops for p, ops in spec["paths"].items() if p.startswith("/api/v1/google-search-console")
    }
    assert len(paths) == 13
    for path, operations in paths.items():
        assert set(operations) == {"get"}, f"{path} is not read-only"
    from app.core.permissions.catalog import all_permissions

    keys = {spec.key for spec in all_permissions()}
    assert {"google_search_console.site.read", "google_search_console.report.run"} <= keys


async def test_the_site_travels_as_a_query_parameter_and_reaches_google_whole(
    client_for, fake
) -> None:
    """A ``siteUrl`` has a scheme and slashes in it, and a path parameter is decoded before it
    is matched — so the property is ``?site=`` and the route has to hand it to Google intact."""
    t = await _connected("gsc-route")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.get(
            "/api/v1/google-search-console/sitemaps",
            params={"site": PREFIX_SITE},
            headers=headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["site_url"] == PREFIX_SITE
        ai = await c.get(
            "/api/v1/google-search-console/ai-visibility",
            params={"site": "klant.nl"},
            headers=headers,
        )
        assert ai.status_code == 200, ai.text
        assert ai.json()["available"] is False
        assert ai.json()["report_url"].endswith("?resource_id=sc-domain%3Aklant.nl")
        # The owner holds `*`, so this asserts the *route's* declaration rather than a grant.
        run = await c.get(
            "/api/v1/google-search-console/query",
            params={"site": PREFIX_SITE, "dimensions": ["query"]},
            headers=headers,
        )
        assert run.status_code == 200, run.text
    assert fake.requests[0][0].endswith("/sites/https://www.klant.nl//sitemaps")


# --- the MCP section ------------------------------------------------------------------------ #
async def test_the_section_is_derived_from_this_router_and_bleeds_into_no_other() -> None:
    """``/api/v1/google-search-console`` shares a first segment with three other Google
    routers, and a plain ``startswith`` would fold it into ``/google`` silently."""
    from app.core.mcp.sections import build_sections
    from app.core.mcp.server import _tool_index
    from app.main import app

    _, paths = _tool_index(app)
    sections = build_sections(paths)
    section = sections["google-search-console"]
    assert section.kind == "module"
    assert section.label_key == "module.google_search_console.label"
    assert len(section.tools) == 13
    assert not (section.tools & sections["google"].tools)
    assert not (section.tools & sections["google-ads"].tools)
    assert not (section.tools & sections["google-analytics"].tools)
    # And the bundle unions it rather than naming any of its tools.
    assert section.tools <= sections["growth"].tools


# --- the assistant's own catalog ------------------------------------------------------------ #
async def test_the_assistant_is_offered_search_console_tools_only_with_the_permission() -> None:
    t = await _connected("gsc-assistant-rbac")
    session, ctx = await _ctx(t, PermissionSet.of([]))
    try:
        none_held = {spec.name for spec in available_tools(ctx)}
        ctx_read = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        with_read = {spec.name for spec in available_tools(ctx_read)}
        ctx_run = RequestContext(user=t.user, org=t.org, session=session, permissions=_RUN)
        with_run = {spec.name for spec in available_tools(ctx_run)}
    finally:
        await session.__aexit__(None, None, None)

    offered = {spec.name for spec in GOOGLE_SEARCH_CONSOLE_MCP_TOOLS}
    assert not (offered & none_held)
    assert "google_search_console.ai_visibility" in with_read
    assert "google_search_console.query" not in with_read
    assert "google_search_console.query" in with_run


# --- the marketing dashboard's card ---------------------------------------------------------- #
async def test_the_dashboard_card_reads_the_same_seam_as_the_integration() -> None:
    """One function owns the report's URL and the "is it in the API" fact, so the card on the
    marketing dashboard and the assistant's tool can never point two ways."""
    from app.modules.marketing.sources.base import source_for

    adapter = source_for("gsc")
    card = adapter.ai_visibility(SITE, {})
    assert card == {"available": False, "report_url": generative_ai_report_url(SITE)}
    assert adapter.deep_link(SITE, {}) == (
        "https://search.google.com/search-console?resource_id=sc-domain%3Aklant.nl"
    )
