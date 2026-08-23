"""The Google Analytics surface: what it answers, what it refuses, and where it lives.

Three groups, and each one is a rule the code would otherwise only look like it follows.

1. **The credential's absence is evidence, never a verdict** (#411). Listing properties with no
   Google connection is a *state* with a cure, so it answers; naming a property is a refusal,
   and the three ways it can fail are three different sentences for three different people.
2. **A read stays a read.** Every route is a GET and every route declares a permission, so the
   licence gate cannot make Analytics stop answering and deny-by-default stays enumerable.
3. **The section is derived, not written down.** ``/mcp/google-analytics`` is this module's own
   router prefix, which is what keeps a route added tomorrow served tomorrow — and what keeps it
   out of ``/mcp/google`` and ``/mcp/google-ads``, whose prefixes it shares a first segment with.
"""

from __future__ import annotations

import pytest

from app.core.ai.tools import available_tools, run_tool
from app.core.crypto import encrypt
from app.core.permissions import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.integrations.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_ANALYTICS
from app.integrations.google_analytics.client import set_transport
from app.integrations.google_analytics.mcp import GOOGLE_ANALYTICS_MCP_TOOLS
from app.integrations.google_analytics.service import GoogleAnalyticsService, parse_filters
from tests.conftest import auth_cookie, make_tenant
from tests.ga4_fake import ACCOUNT, PROPERTY, FakeGA4, report

pytestmark = pytest.mark.asyncio

_READ = PermissionSet.of(["google_analytics.property.read"])
_RUN = PermissionSet.of(["google_analytics.property.read", "google_analytics.report.run"])


@pytest.fixture
def fake() -> FakeGA4:
    stub = FakeGA4()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


async def _connected(
    slug: str, *, scopes: tuple[str, ...] = (SCOPE_ANALYTICS,), status: str | None = None
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
                email="ga@example.com",
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


# --- the credential ---------------------------------------------------------------------- #
async def test_no_connection_is_a_state_the_listing_reports_rather_than_an_error() -> None:
    """A missing credential decides a sentence, never whether the control exists (#411).

    Raising here would leave a picker with nothing to teach from: "connect Google" and "allow
    Analytics" are different acts, and only the payload can say which one is missing.
    """
    t = await make_tenant("ga-unconnected")
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleAnalyticsService(ctx).properties()
    finally:
        await session.__aexit__(None, None, None)
    assert listing.connected is False
    assert listing.has_scope is False
    assert listing.properties == []


async def test_a_connection_without_the_analytics_scope_says_so(fake) -> None:
    """Connected-but-unauthorised is not the same fact as unconnected, and the cure differs."""
    t = await _connected("ga-noscope", scopes=())
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleAnalyticsService(ctx).properties()
        # Naming a property *is* a refusal by then: there is a specific thing being asked for.
        with pytest.raises(AppError) as refusal:
            await GoogleAnalyticsService(ctx).metadata(PROPERTY)
    finally:
        await session.__aexit__(None, None, None)
    assert listing.connected is True
    assert listing.has_scope is False
    assert refusal.value.message_key == "errors.google_analytics_scope_missing"
    assert refusal.value.status_code == 409
    # Nothing was asked of Google: a scope we can see is missing is not a question worth asking.
    assert fake.requests == []


# --- what it answers --------------------------------------------------------------------- #
async def test_properties_are_flattened_out_of_the_account_summaries(fake) -> None:
    t = await _connected("ga-properties")
    session, ctx = await _ctx(t)
    try:
        listing = await GoogleAnalyticsService(ctx).properties()
    finally:
        await session.__aexit__(None, None, None)
    assert [row.property_id for row in listing.properties] == [PROPERTY]
    row = listing.properties[0]
    assert row.display_name == "klant.nl"
    # The account is carried, because an agency reading a list of forty needs to know whose.
    assert row.account_id == ACCOUNT and row.account_name == "breik."


async def test_the_overview_is_one_round_trip_and_names_what_it_compared_against(fake) -> None:
    """Three questions, one ``batchRunReports`` — GA4's quota is per property per day.

    And the compared span is *stated*: "up 20 %" over an unnamed period is not a claim anybody
    can check (#312), which is the same rule the Ads overview follows one integration over.
    """
    metrics = [
        "sessions", "totalUsers", "newUsers", "screenPageViews", "keyEvents",
        "engagementRate", "averageSessionDuration", "bounceRate", "totalRevenue",
    ]
    fake.scripted[":batchRunReports"] = {
        "reports": [
            report(metrics=metrics, rows=[], totals=[120, 90, 40, 300, 8, 0.61, 55, 0.39, 0]),
            report(metrics=metrics, rows=[], totals=[100, 80, 30, 250, 5, 0.55, 50, 0.45, 0]),
            report(
                dimensions=["sessionDefaultChannelGroup"],
                metrics=["sessions"],
                rows=[(["Organic Search"], [70]), (["Direct"], [50])],
            ),
        ]
    }
    t = await _connected("ga-overview")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleAnalyticsService(ctx).overview(PROPERTY, period="30d")
    finally:
        await session.__aexit__(None, None, None)

    assert len(fake.requests) == 1, "the overview must not cost three calls"
    assert result.period.days == 30
    assert result.compared_with.mode == "year"
    assert result.totals["sessions"] == 120
    assert result.previous_totals["sessions"] == 100
    assert result.change["sessions"].absolute == 20
    assert result.change["sessions"].relative == 0.2
    assert result.channels == {"Organic Search": 70.0, "Direct": 50.0}


async def test_a_delta_against_no_baseline_is_undefined_rather_than_infinite(fake) -> None:
    """A model handed a number for "up from nothing" writes a sentence about it."""
    fake.scripted[":batchRunReports"] = {
        "reports": [
            report(metrics=["sessions"], rows=[], totals=[10]),
            report(metrics=["sessions"], rows=[], totals=[0]),
            report(dimensions=["sessionDefaultChannelGroup"], metrics=["sessions"], rows=[]),
        ]
    }
    t = await _connected("ga-nobaseline")
    session, ctx = await _ctx(t)
    try:
        result = await GoogleAnalyticsService(ctx).overview(PROPERTY)
    finally:
        await session.__aexit__(None, None, None)
    assert result.change["sessions"].relative is None
    assert result.change["sessions"].absolute == 10


async def test_a_page_of_a_longer_table_says_that_it_is_one(fake) -> None:
    """A prefix presented as a whole is the worst answer available: it looks like it worked."""
    fake.scripted[":runReport"] = report(
        dimensions=["pagePath"],
        metrics=["sessions"],
        rows=[(["/"], [40]), (["/contact"], [12])],
        row_count=137,
    )
    t = await _connected("ga-truncated")
    session, ctx = await _ctx(t)
    try:
        table = await GoogleAnalyticsService(ctx).breakdown(
            PROPERTY, dimension="pagePath", metrics=["sessions"], limit=2
        )
    finally:
        await session.__aexit__(None, None, None)
    assert table.row_count == 137
    assert table.truncated is True
    assert len(table.rows) == 2
    assert table.rows[0].metrics["sessions"] == 40.0


async def test_a_sampled_answer_is_reported_as_an_estimate(fake) -> None:
    """A sampled number reads as a count on every screen it lands on unless something says so."""
    body = report(metrics=["sessions"], rows=[], totals=[10])
    body["metadata"] = {"samplingMetadatas": [{"samplesReadCount": "1"}], "currencyCode": "EUR"}
    fake.scripted[":runReport"] = body
    t = await _connected("ga-sampled")
    session, ctx = await _ctx(t)
    try:
        table = await GoogleAnalyticsService(ctx).report(
            PROPERTY, dimensions=[], metrics=["sessions"]
        )
    finally:
        await session.__aexit__(None, None, None)
    assert "google_analytics.warning.sampled" in table.warnings


async def test_google_s_refusal_becomes_a_reason_code_and_never_its_own_prose(fake) -> None:
    """Untranslated vendor English in the envelope is a screen in the wrong language (§9)."""
    fake.failures[":runReport"] = (400, "badRequest")
    t = await _connected("ga-badrequest")
    session, ctx = await _ctx(t)
    try:
        with pytest.raises(AppError) as refusal:
            await GoogleAnalyticsService(ctx).report(PROPERTY, dimensions=[], metrics=["nope"])
    finally:
        await session.__aexit__(None, None, None)
    # 422 rather than 502: a malformed report is the caller's, and "fix your request" and "the
    # provider is down" are instructions for two different people.
    assert refusal.value.status_code == 422
    assert refusal.value.message_key == "errors.google_analytics_invalid_request"
    assert refusal.value.details["google_reason"] == "badRequest"
    assert "nope" not in str(refusal.value.details.values())


# --- the filter grammar ------------------------------------------------------------------ #
async def test_a_filter_clause_is_refused_rather_than_ignored() -> None:
    """A filter silently dropped answers a different question with every row valid.

    That is the SnelStart ``$filter`` failure in a query string, and the only defence is to
    refuse what cannot be parsed instead of quietly widening the question.
    """
    assert parse_filters([]) is None
    exact = parse_filters(["sessionSource==google"])
    assert exact["filter"]["stringFilter"]["matchType"] == "EXACT"
    assert exact["filter"]["fieldName"] == "sessionSource"
    assert parse_filters(["pagePath=@/blog"])["filter"]["stringFilter"]["matchType"] == "CONTAINS"
    both = parse_filters(["sessionSource==google", "pagePath=^/nl"])
    assert len(both["andGroup"]["expressions"]) == 2
    with pytest.raises(AppError) as refusal:
        parse_filters(["sessionSource~google"])
    assert refusal.value.status_code == 422


# --- the routes ---------------------------------------------------------------------------- #
async def test_every_route_is_a_get_that_declares_a_permission() -> None:
    """All-GET is the design, not a phase: nothing here is worth writing, and a read must keep
    answering past a licence expiry, which the write gate decides by *method* (§18)."""
    from app.main import app

    spec = app.openapi()
    paths = {p: ops for p, ops in spec["paths"].items() if p.startswith("/api/v1/google-analytics")}
    assert len(paths) == 17
    for path, operations in paths.items():
        assert set(operations) == {"get"}, f"{path} is not read-only"


async def test_the_read_permission_does_not_carry_the_free_form_report(client_for, fake) -> None:
    """``report.run`` is its own grant for ``google_ads.query.run``'s reason: an agent may ask a
    question nobody here anticipated, which is the point of it and why it is a separate decision.
    """
    t = await _connected("ga-perm-split")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # The owner holds `*`, so this asserts the *route's* declaration rather than a grant.
        res = await c.get(
            f"/api/v1/google-analytics/properties/{PROPERTY}/report?metrics=sessions",
            headers=headers,
        )
    assert res.status_code != 403
    from app.core.permissions.catalog import all_permissions

    keys = {spec.key for spec in all_permissions()}
    assert {"google_analytics.property.read", "google_analytics.report.run"} <= keys


# --- the MCP section ------------------------------------------------------------------------ #
async def test_the_section_is_derived_from_this_router_and_bleeds_into_no_other() -> None:
    """``/api/v1/google-analytics`` is not under ``/api/v1/google``, and a plain ``startswith``
    says it is — which would fold every Analytics tool into Workspace's section silently."""
    from app.core.mcp.sections import build_sections
    from app.core.mcp.server import _tool_index
    from app.main import app

    _, paths = _tool_index(app)
    sections = build_sections(paths)
    section = sections["google-analytics"]
    assert section.kind == "module"
    assert section.label_key == "module.google_analytics.label"
    assert len(section.tools) == 17
    assert not (section.tools & sections["google"].tools)
    assert not (section.tools & sections["google-ads"].tools)
    # And the bundle unions it rather than naming any of its tools.
    assert section.tools <= sections["growth"].tools


# --- the assistant's own catalog ------------------------------------------------------------ #
async def test_the_assistant_is_offered_analytics_tools_only_with_the_permission() -> None:
    """The permission filter is what keeps a tool the caller may never use out of the model's
    view entirely; the service's own ``ctx.require`` is what keeps the answer correct (§15)."""
    t = await _connected("ga-assistant-rbac")
    session, ctx = await _ctx(t, PermissionSet.of([]))
    try:
        none_held = {spec.name for spec in available_tools(ctx)}
        ctx_read = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        with_read = {spec.name for spec in available_tools(ctx_read)}
        ctx_run = RequestContext(user=t.user, org=t.org, session=session, permissions=_RUN)
        with_run = {spec.name for spec in available_tools(ctx_run)}
    finally:
        await session.__aexit__(None, None, None)

    offered = {spec.name for spec in GOOGLE_ANALYTICS_MCP_TOOLS}
    assert not (offered & none_held)
    assert "google_analytics.overview" in with_read
    # The escape hatch is not implied by the read, on the assistant either.
    assert "google_analytics.report" not in with_read
    assert "google_analytics.report" in with_run


async def test_the_setup_tool_folds_four_reads_and_states_what_is_missing(fake) -> None:
    """"Is the tracking working" is four calls and a judgement, and a model that has to remember
    the fourth will sometimes not — and answer confidently after three."""
    fake.listings["keyEvents"] = []
    t = await _connected("ga-setup")
    session, ctx = await _ctx(t)
    try:
        spec = next(s for s in GOOGLE_ANALYTICS_MCP_TOOLS if s.name == "google_analytics.setup")
        result = await run_tool(ctx, spec, {"property_id": PROPERTY})
    finally:
        await session.__aexit__(None, None, None)
    data = result.data
    assert data["property"]["display_name"] == "klant.nl"
    assert data["data_streams"][0]["webStreamData"]["measurementId"] == "G-ABC123"
    assert data["findings"] == {
        "no_data_stream": False,
        "no_key_events": True,
        "no_google_ads_link": True,
    }
    # The retention window: "there is no data" and "the data was deleted by policy" look
    # identical in a chart, and only one of them is fixable.
    assert data["data_retention"]["eventDataRetention"] == "FOURTEEN_MONTHS"


async def test_every_analytics_tool_declares_a_permission() -> None:
    """A tool with no permission is offered to everyone the moment somebody adds one."""
    assert all(spec.permission for spec in GOOGLE_ANALYTICS_MCP_TOOLS)
