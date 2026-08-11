"""The curated MCP tools.

Every `/api/v1` route is already a tool, so these three exist only because a single call beats
three plus arithmetic the model should not be doing. What is asserted here is exactly that extra
work: the comparison span is *named*, a delta against zero is undefined rather than infinite, and
a term that is already excluded is never proposed for exclusion again.
"""

from __future__ import annotations

import pytest

from app.core.ai.tools import available_tools, run_tool
from app.core.permissions import PermissionSet
from app.core.tenancy import RequestContext
from app.db import async_session_maker, set_current_org
from app.modules.google_ads.mcp import GOOGLE_ADS_MCP_TOOLS, _delta
from tests.conftest import make_tenant
from tests.googleads_fake import campaign_row, search_term_row
from tests.test_google_ads_reads import _linked, fake  # noqa: F401 — the transport fixture

pytestmark = pytest.mark.asyncio

_READ = PermissionSet.of(["google_ads.account.read"])


def _spec(name: str):
    return next(spec for spec in GOOGLE_ADS_MCP_TOOLS if spec.name == name)


async def test_the_overview_tool_names_the_span_it_compared_against(fake) -> None:  # noqa: F811
    """"Up 20 %" over an unnamed period is not a claim anyone can check (#312)."""
    t, account_id = await _linked("gads-mcp-overview")
    fake.script(
        "FROM campaign",
        [campaign_row(1, "Merk", clicks=100, cost_micros=100_000_000, conversions=10)],
    )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        result = await run_tool(
            ctx, _spec("google_ads.overview"), {"account_id": str(account_id), "period": "30d"}
        )
    data = result.data
    # The default is a year earlier — the comparison seasonality survives (#312).
    assert data["compared_with"]["mode"] == "year"
    assert data["compared_with"]["from"] and data["compared_with"]["to"]
    assert data["period"]["days"] == 30
    # The same rows are scripted for both windows, so the delta must be exactly nothing.
    assert data["change"]["clicks"]["absolute"] == 0
    assert data["change"]["clicks"]["from"] == 100


async def test_a_delta_against_zero_is_undefined_not_infinite() -> None:
    """A model handed `inf` will write a sentence about it."""
    assert _delta(5, 0)["relative"] is None
    assert _delta(5, 4)["relative"] == 0.25
    assert _delta(4, 5)["absolute"] == -1
    assert _delta(None, 4) is None


async def test_wasted_spend_never_proposes_what_is_already_excluded(fake) -> None:  # noqa: F811
    """The cross-reference is the part a model gets wrong, so the tool does it once."""
    t, account_id = await _linked("gads-mcp-wasted")
    fake.script(
        "FROM search_term_view",
        [
            search_term_row("gratis offerte", cost_micros=40_000_000, clicks=20),
            search_term_row("vacature monteur", cost_micros=30_000_000, clicks=15),
            search_term_row("al uitgesloten", cost_micros=20_000_000, clicks=10),
            search_term_row(
                "google zegt uitgesloten", status="EXCLUDED", cost_micros=10_000_000, clicks=5
            ),
            search_term_row("converteert", cost_micros=50_000_000, clicks=25, conversions=3),
        ],
    )
    fake.script(
        "FROM ad_group_criterion",
        [
            {
                "campaign": {"id": "1", "name": "Zoeken"},
                "adGroup": {"id": "2", "name": "Merk"},
                # Stored with different casing than the search term, on purpose.
                "adGroupCriterion": {
                    "criterionId": "9",
                    "keyword": {"text": "Al Uitgesloten", "matchType": "EXACT"},
                },
            }
        ],
    )
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        result = await run_tool(
            ctx,
            _spec("google_ads.wasted_spend"),
            {"account_id": str(account_id), "period": "30d"},
        )
    terms = [row["search_term"] for row in result.data["terms"]]
    assert terms == ["gratis offerte", "vacature monteur"]
    assert result.data["wasted_cost"] == 70.0
    # Three separate reasons a term is not a candidate, all of them enforced:
    assert "al uitgesloten" not in terms  # already a negative, different casing
    assert "google zegt uitgesloten" not in terms  # Google's own EXCLUDED status
    assert "converteert" not in terms  # it converted, however much it cost
    assert "google_ads.warning.wasted_spend_is_a_shortlist" in result.data["warnings"]


async def test_the_tools_are_hidden_from_a_caller_who_cannot_read_ads() -> None:
    """A tool the caller may never use is kept out of the model's view entirely, rather than
    offered and then refused — a refusal the model would try to work around."""
    t = await make_tenant("gads-mcp-perms")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        nothing = RequestContext(
            user=t.user, org=t.org, session=session, permissions=PermissionSet.of([])
        )
        reader = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        hidden = {spec.name for spec in available_tools(nothing)}
        offered = {spec.name for spec in available_tools(reader)}
    ads_tools = {spec.name for spec in GOOGLE_ADS_MCP_TOOLS}
    assert ads_tools & hidden == set()
    assert ads_tools <= offered


async def test_an_unconfigured_account_answers_the_model_rather_than_raising(fake) -> None:  # noqa: F811
    """`run_tool` turns an exception into a 500 the model cannot read. A presentable state has
    to arrive as data."""
    from sqlalchemy import select

    from app.modules.google_ads.models import GoogleAdsAccount

    t, account_id = await _linked("gads-mcp-unconfigured")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
        account.connection_id = None
        await session.commit()
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        result = await run_tool(
            ctx, _spec("google_ads.overview"), {"account_id": str(account_id)}
        )
    assert result.data == {"error": "errors.google_ads_not_configured"}


async def test_the_accounts_tool_grounds_a_client_name_to_an_id(fake) -> None:  # noqa: F811
    """Every other tool takes an account_id; an assistant asked about "AAZET" needs this one."""
    t, account_id = await _linked("gads-mcp-accounts")
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        ctx = RequestContext(user=t.user, org=t.org, session=session, permissions=_READ)
        found = await run_tool(ctx, _spec("google_ads.accounts"), {"query": "aazet"})
        missing = await run_tool(ctx, _spec("google_ads.accounts"), {"query": "bestaat niet"})
    assert [a["account_id"] for a in found.data["accounts"]] == [str(account_id)]
    assert found.data["accounts"][0]["customer_id"] == "124-264-3293"
    assert missing.data["accounts"] == []
