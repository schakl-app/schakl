"""The marketing tools the in-app assistant is offered (#127, §12).

Every ``/api/v1`` route is already an MCP tool, so what is asserted here is the *other* catalog:
the six shapes the assistant gets, whether they are read-only, and — the part that is a rule
rather than a preference — whether the permission each one declares is the permission its
service is about to demand.

That last one is what makes RBAC real on this surface. ``ctx.can`` filters the tools before the
model is told they exist; the service's own ``ctx.require`` refuses a call that got through
anyway. Testing only the second would leave a tool visible to somebody who may never use it, and
testing only the first would leave the filter as the whole defence, which it must never be.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.core.ai.tools import available_tools, run_tool
from app.core.permissions import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.errors import AppError
from app.modules.marketing.mcp import MARKETING_MCP_TOOLS
from app.modules.marketing.models import MarketingLink, MarketingMetricDaily
from tests.conftest import make_tenant, org_today

pytestmark = pytest.mark.asyncio

_METRICS = PermissionSet.of(["marketing.metrics.read"])
_OVERVIEW = PermissionSet.of(["marketing.metrics.read", "marketing.overview.read"])


def _spec(name: str):
    return next(spec for spec in MARKETING_MCP_TOOLS if spec.name == name)


async def _linked(slug: str, *, company_name: str = "Klant BV"):
    """An org with one client, one GA4 link and thirty days of stored metrics on both sides of
    the year-earlier comparison the platform defaults to (#312)."""
    t = await make_tenant(slug)
    today = org_today()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        company_id = (
            await session.execute(
                text(
                    "INSERT INTO companies (id, org_id, name, status, created_at, updated_at) "
                    "VALUES (:id, :org, :name, 'active', now(), now()) RETURNING id"
                ),
                {"id": uuid.uuid4(), "org": t.org.id, "name": company_name},
            )
        ).scalar_one()
        link = MarketingLink(
            org_id=t.org.id,
            company_id=company_id,
            source="ga4",
            external_id="properties/1",
            display_name="klant.nl",
            active=True,
            backfill_done=True,
            last_synced_at=datetime.now(UTC),
        )
        session.add(link)
        await session.flush()
        for offset in range(1, 31):
            session.add(
                MarketingMetricDaily(
                    org_id=t.org.id,
                    link_id=link.id,
                    date=today - timedelta(days=offset),
                    metrics={"sessions": 10.0, "totalUsers": 8.0},
                    synced_at=datetime.now(UTC),
                )
            )
        await session.commit()
        return t, company_id, link.id


async def _ctx(t, permissions: PermissionSet):
    session = async_session_maker()
    await session.__aenter__()
    await set_current_org(session, t.org.id)
    return session, RequestContext(
        user=t.user, org=t.org, session=session, permissions=permissions
    )


async def test_every_marketing_tool_is_a_read_and_declares_its_permission() -> None:
    """The writes this module owns — linking a client to somebody else's advertising account,
    rewording a dashboard — are configuration somebody makes once. A model must not reach for
    one because a sentence sounded like a request."""
    assert {spec.name for spec in MARKETING_MCP_TOOLS} == {
        "marketing.clients",
        "marketing.performance",
        "marketing.drilldown",
        "marketing.connections",
        "marketing.summary",
        "marketing.overview",
    }
    assert all(spec.permission for spec in MARKETING_MCP_TOOLS)
    assert all(
        spec.permission in {"marketing.metrics.read", "marketing.overview.read"}
        for spec in MARKETING_MCP_TOOLS
    )


async def test_the_cross_client_grid_is_not_offered_on_the_per_client_read() -> None:
    """``marketing.overview.read`` is a manager permission here (docs/UX.md), and the portal
    ``client`` role holds the metrics read — so folding the two would put every client's numbers
    behind a tool one client's login can reach."""
    t, _, _ = await _linked("mktg-mcp-rbac")
    session, ctx = await _ctx(t, _METRICS)
    try:
        with_metrics = {spec.name for spec in available_tools(ctx)}
        ctx_all = RequestContext(
            user=t.user, org=t.org, session=session, permissions=_OVERVIEW
        )
        with_overview = {spec.name for spec in available_tools(ctx_all)}
        none_ctx = RequestContext(
            user=t.user, org=t.org, session=session, permissions=PermissionSet.of([])
        )
        with_none = {spec.name for spec in available_tools(none_ctx)}
    finally:
        await session.__aexit__(None, None, None)
    assert "marketing.performance" in with_metrics
    assert "marketing.overview" not in with_metrics
    assert "marketing.overview" in with_overview
    assert not ({spec.name for spec in MARKETING_MCP_TOOLS} & with_none)


async def test_the_service_refuses_even_when_the_filter_is_bypassed() -> None:
    """The filter keeps a tool out of the model's view; the service keeps the answer correct.

    ``run_tool`` turns an ``AppError`` into data the model can read rather than a 500 — so the
    assertion is that the refusal *arrives*, not that it raises.
    """
    t, _, _ = await _linked("mktg-mcp-bypass")
    session, ctx = await _ctx(t, _METRICS)
    try:
        result = await run_tool(ctx, _spec("marketing.overview"), {})
    finally:
        await session.__aexit__(None, None, None)
    assert result.data == {"error": "errors.forbidden"} or "error" in result.data


async def test_clients_grounds_a_name_into_an_id() -> None:
    """Every other marketing tool takes a company_id, and nobody types one from memory."""
    t, company_id, _ = await _linked("mktg-mcp-clients", company_name="AAZET")
    session, ctx = await _ctx(t, _METRICS)
    try:
        result = await run_tool(ctx, _spec("marketing.clients"), {"query": "aaz"})
        empty = await run_tool(ctx, _spec("marketing.clients"), {"query": "geen klant"})
    finally:
        await session.__aexit__(None, None, None)
    assert [row["company_id"] for row in result.data["clients"]] == [str(company_id)]
    assert result.data["clients"][0]["sources"][0]["source"] == "ga4"
    # The chips are what make an answer clickable back to the record it came from.
    assert result.sources[0].type == "company"
    assert empty.data["clients"] == []


async def test_performance_names_the_span_it_compared_against() -> None:
    """"Up 20 %" over an unnamed period is not a claim anybody can check (#312) — and this
    client may compare against last year while the next one does not."""
    t, company_id, link_id = await _linked("mktg-mcp-performance")
    session, ctx = await _ctx(t, _METRICS)
    try:
        result = await run_tool(
            ctx,
            _spec("marketing.performance"),
            {"company_id": str(company_id), "period": "30d"},
        )
    finally:
        await session.__aexit__(None, None, None)
    data = result.data
    assert data["period"]["days"] == 30
    assert data["compared_with"]["mode"] in {"year", "previous"}
    assert data["compared_with"]["from"] and data["compared_with"]["to"]
    source = data["sources"][0]
    assert source["link_id"] == str(link_id)
    assert source["source"] == "ga4"
    # Health rides along, and this fixture is exactly why it has to: the link's Google
    # connection is gone, so the numbers are the last ones synced rather than the current ones.
    # A model that cannot tell "no traffic" from "nobody is looking any more" reports the first.
    assert source["health"] == "disconnected"
    assert source["kpis"]["sessions"]["current"] == 300.0
    # The daily series is thousands of numbers answering a question nobody asked out loud.
    assert "series" not in source


async def test_the_daily_series_is_opt_in_and_capped() -> None:
    t, company_id, _ = await _linked("mktg-mcp-series")
    session, ctx = await _ctx(t, _METRICS)
    try:
        result = await run_tool(
            ctx,
            _spec("marketing.performance"),
            {"company_id": str(company_id), "period": "30d", "include_series": True},
        )
    finally:
        await session.__aexit__(None, None, None)
    series = result.data["sources"][0]["series"]
    assert len(series["dates"]) <= 92
    assert series["dates"]


async def test_a_company_outside_the_tenant_is_not_found() -> None:
    """Every handler goes through the module's own service, so Golden Rule 1 holds by
    construction rather than by this file remembering it."""
    t, _, _ = await _linked("mktg-mcp-tenant-a")
    other, other_company, _ = await _linked("mktg-mcp-tenant-b")
    session, ctx = await _ctx(t, _METRICS)
    try:
        result = await run_tool(
            ctx, _spec("marketing.performance"), {"company_id": str(other_company)}
        )
    finally:
        await session.__aexit__(None, None, None)
    assert result.data == {"error": "errors.not_found"}


async def test_a_malformed_id_is_a_refusal_the_model_can_read() -> None:
    t, _, _ = await _linked("mktg-mcp-badid")
    session, ctx = await _ctx(t, _METRICS)
    try:
        result = await run_tool(ctx, _spec("marketing.connections"), {"company_id": "nope"})
    finally:
        await session.__aexit__(None, None, None)
    assert result.data["error"] == "errors.validation"
    assert isinstance(AppError("x", "y"), Exception)
