"""meta_ads: reading and changing a client's Meta advertising — through the fake transport.

What is asserted beyond "it works" is the structure that makes an agent with a key safe:
that **nothing can be created switched on**, that switching on is a permission of its own,
that a budget is judged before it is sent and the refusal names the limit, that an id from
outside is resolved inside the account it was named with, and that every change is written
down with who made it and why.
"""

from __future__ import annotations

import base64

import pytest

from app.core.metagraph import set_transport
from app.integrations.meta_ads import policy as rules
from tests.conftest import auth_cookie, make_tenant
from tests.meta_fake import AD_ACCOUNT_CLIENT, AD_ACCOUNT_OWN, PAGE_CLIENT, FakeMeta, error
from tests.meta_helpers import BASE, image, member, ready

pytestmark = pytest.mark.asyncio

ADS = "/api/v1/meta-ads"

TARGETING = {"countries": ["NL"], "age_min": 25, "age_max": 55}


@pytest.fixture
def fake() -> FakeMeta:
    stub = FakeMeta()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


async def _campaign(c, r, **body) -> dict:  # noqa: ANN001
    payload = {"name": "Najaarscampagne", "objective": "OUTCOME_TRAFFIC", "reason": "Briefing"}
    payload.update(body)
    res = await c.post(
        f"{ADS}/accounts/{r.ad_account_id}/campaigns", json=payload, headers=r.headers
    )
    assert res.status_code == 201, res.text
    return res.json()


async def _adset(c, r, campaign_id: str, **body) -> dict:  # noqa: ANN001
    payload = {
        "campaign_meta_id": campaign_id,
        "name": "Nederland 25-55",
        "optimization_goal": "LINK_CLICKS",
        "daily_budget_cents": 2_500,
        "targeting": TARGETING,
    }
    payload.update(body)
    res = await c.post(f"{ADS}/accounts/{r.ad_account_id}/adsets", json=payload, headers=r.headers)
    assert res.status_code == 201, res.text
    return res.json()


# --- pure rules ----------------------------------------------------------------------------- #


async def test_the_only_built_in_ceiling_is_relative() -> None:
    """A default may only live in the layer that needs no local knowledge: "may at most
    double" catches the extra zero in any currency; any absolute figure would not."""
    policy = rules.resolve(None, None)
    assert policy.max_daily_budget is None
    assert rules.budget_refusal(policy, kind="daily", new=5_000, previous=2_500) is None
    refusal = rules.budget_refusal(policy, kind="daily", new=25_000, previous=2_500)
    assert refusal.code == "errors.meta_ads_budget_increase_too_large"
    assert refusal.details == {"limit": 5_000, "value": 25_000, "previous": 2_500}
    # A create has no previous amount, so only an absolute ceiling bounds it.
    assert rules.budget_refusal(policy, kind="daily", new=9_999_999, previous=None) is None
    # And lowering a budget is never refused.
    assert rules.budget_refusal(policy, kind="daily", new=1, previous=2_500) is None


async def test_a_banned_phrase_is_a_whole_word() -> None:
    class Row:
        banned_phrases = ["gratis"]
        max_daily_budget = None
        max_lifetime_budget = None
        max_budget_increase = None
        dsa_beneficiary = None
        dsa_payor = None
        steering = ""

    policy = rules.resolve(None, Row())
    assert rules.phrase_refusal(policy, {"message": "Nu GRATIS verzending!"}).field == "message"
    assert rules.phrase_refusal(policy, {"message": "Een gratisje"}) is None


async def test_the_dsa_names_come_from_the_most_specific_statement() -> None:
    policy = rules.resolve(None, None)
    assert rules.dsa_names(
        requested_beneficiary=None,
        requested_payor="Bureau B.V.",
        policy=policy,
        account_beneficiary="Account B.V.",
        account_payor="Account B.V.",
        client_name="Klant B.V.",
    ) == ("Account B.V.", "Bureau B.V.")
    assert rules.dsa_names(
        requested_beneficiary=None,
        requested_payor=None,
        policy=policy,
        account_beneficiary=None,
        account_payor=None,
        client_name=None,
    ) == (None, None)


# --- reads ---------------------------------------------------------------------------------- #


async def test_the_accounts_are_the_linked_ad_accounts(client_for, fake) -> None:
    r = await ready(client_for, "ads-accounts")
    async with client_for(r.tenant.host) as c:
        res = await c.get(f"{ADS}/accounts", headers=r.headers)
        page = await c.get(f"{ADS}/accounts/{r.page_id}", headers=r.headers)
    body = res.json()
    assert [a["meta_id"] for a in body] == [AD_ACCOUNT_CLIENT]
    assert body[0]["company_name"] == "Nova Fietsen"
    assert body[0]["currency"] == "EUR"
    assert body[0]["can_write"] is True
    # A Page is not an ad account, and says so exactly as an unknown id would.
    assert page.status_code == 404


async def test_the_client_hub_lists_the_accounts_and_asks_meta_nothing(client_for, fake) -> None:
    r = await ready(client_for, "ads-hub")
    async with client_for(r.tenant.host) as c:
        before = len(fake.calls)
        hub = await c.get(f"/api/v1/companies/{r.company_id}/panels", headers=r.headers)
        assert len(fake.calls) == before
    panel = next(p for p in hub.json() if p["key"] == "meta_ads.company")
    assert panel["empty"] is False
    assert [a["meta_id"] for a in panel["data"]["items"]] == [AD_ACCOUNT_CLIENT]


async def test_the_stored_account_costs_no_call_and_the_live_one_is_metas(client_for, fake) -> None:
    """The layout of every ads screen reads the account, so that read is the stored row. What
    Meta says *right now* is its own route, asked for by the screen that prints it."""
    r = await ready(client_for, "ads-stored-live")
    async with client_for(r.tenant.host) as c:
        before = len(fake.calls)
        stored = await c.get(f"{ADS}/accounts/{r.ad_account_id}", headers=r.headers)
        assert len(fake.calls) == before
        live = await c.get(f"{ADS}/accounts/{r.ad_account_id}/live", headers=r.headers)
        assert len(fake.calls) > before
    assert stored.status_code == 200, stored.text
    assert stored.json()["meta_id"] == AD_ACCOUNT_CLIENT
    assert stored.json()["ads_manager_url"].startswith("https://")
    assert live.status_code == 200, live.text
    assert live.json()["meta_id"] == AD_ACCOUNT_CLIENT
    assert "amount_spent_cents" in live.json()


async def test_insights_are_numbers_and_the_totals_are_metas_own(client_for, fake) -> None:
    """Meta sends every figure as a string, and ``ctr`` cannot be added up: the totals are
    the account's own row, never a sum of the column."""
    r = await ready(client_for, "ads-insights")
    fake.insight_rows = [
        {
            "account_id": AD_ACCOUNT_CLIENT,
            "campaign_id": "1",
            "campaign_name": "Een",
            "spend": "12.50",
            "impressions": "1000",
            "reach": "800",
            "clicks": "40",
            "ctr": "4.0",
            "cpc": "0.31",
            "cpm": "12.5",
            "actions": [{"action_type": "link_click", "value": "38"}],
            "date_start": "2026-08-01",
            "date_stop": "2026-08-31",
        },
        {
            "account_id": AD_ACCOUNT_CLIENT,
            "campaign_id": "2",
            "campaign_name": "Twee",
            "spend": "7.50",
            "impressions": "3000",
            "clicks": "30",
            "ctr": "1.0",
            "date_start": "2026-08-01",
            "date_stop": "2026-08-31",
        },
    ]
    async with client_for(r.tenant.host) as c:
        res = await c.get(
            f"{ADS}/accounts/{r.ad_account_id}/insights",
            params={"period": "2026-08"},
            headers=r.headers,
        )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["date_from"] == "2026-08-01" and body["date_to"] == "2026-08-31"
    assert body["currency"] == "EUR"
    first = body["rows"][0]
    assert first["meta_id"] == "1" and first["name"] == "Een"
    assert first["spend"] == 12.5 and first["impressions"] == 1000
    assert first["actions"] == {"link_click": 38.0}
    assert body["rows"][1]["reach"] is None
    assert body["totals"] is not None
    read = [c for c in fake.calls if c.path.endswith("/insights")]
    assert [c.params["level"] for c in read] == ["campaign", "account"]
    assert '"since":"2026-08-01"' in read[0].params["time_range"]


# --- creating ------------------------------------------------------------------------------- #


async def test_a_campaign_is_created_paused_whatever_the_caller_sends(client_for, fake) -> None:
    """No create reads a status from its caller, and the schema has no ``ACTIVE`` to send:
    a vocabulary with no room for the dangerous value."""
    r = await ready(client_for, "ads-paused")
    async with client_for(r.tenant.host) as c:
        result = await _campaign(c, r, status="ACTIVE")
        listed = (
            await c.get(f"{ADS}/accounts/{r.ad_account_id}/campaigns", headers=r.headers)
        ).json()
    assert result["applied"] is True
    assert result["status"] == "PAUSED"
    write = fake.writes(f"act_{AD_ACCOUNT_CLIENT}/campaigns")[0]
    assert write.data["status"] == "PAUSED"
    assert write.data["special_ad_categories"] == "[]"
    assert write.data["is_adset_budget_sharing_enabled"] == "false"
    assert listed[0]["meta_id"] == result["meta_id"]
    assert listed[0]["status"] == "PAUSED"


async def test_validate_only_changes_nothing_and_records_nothing(client_for, fake) -> None:
    r = await ready(client_for, "ads-validate")
    async with client_for(r.tenant.host) as c:
        result = await _campaign(c, r, validate_only=True)
        decisions = (
            await c.get(f"{ADS}/accounts/{r.ad_account_id}/decisions", headers=r.headers)
        ).json()
    assert result == {
        "meta_id": None,
        "kind": "campaigns",
        "applied": False,
        "validate_only": True,
        "status": None,
        "decision_id": None,
    }
    assert fake.campaigns == {}
    assert decisions["total"] == 0
    assert "validate_only" in fake.writes("/campaigns")[0].data["execution_options"]


async def test_a_write_is_never_retried(client_for, fake) -> None:
    """A retried create is a second campaign on a second budget."""
    r = await ready(client_for, "ads-noretry")
    fake.fail_next("POST", "/campaigns", error(2, "Temporarily unavailable", status=500))
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns",
            json={"name": "x", "objective": "OUTCOME_TRAFFIC"},
            headers=r.headers,
        )
    assert res.status_code == 502
    assert len(fake.writes("/campaigns")) == 1
    assert fake.campaigns == {}


async def test_an_ad_set_for_the_eu_carries_the_two_names(client_for, fake) -> None:
    """Every ad set a Dutch agency makes targets the EU, so the two names the Digital
    Services Act asks for are filled in — here from the account's own defaults at Meta."""
    r = await ready(client_for, "ads-dsa")
    async with client_for(r.tenant.host) as c:
        campaign = await _campaign(c, r)
        result = await _adset(c, r, campaign["meta_id"])
    write = fake.writes("/adsets")[0]
    assert result["status"] == "PAUSED"
    assert write.data["dsa_beneficiary"] == "Nova Fietsen B.V."
    assert write.data["dsa_payor"] == "Nova Fietsen B.V."
    assert write.data["daily_budget"] == "2500"
    assert '"advantage_audience":0' in write.data["targeting"]
    assert '"countries":["NL"]' in write.data["targeting"]


async def test_without_anybody_to_name_an_eu_ad_set_is_refused(client_for, fake) -> None:
    """The agency's own account has no client and no defaults at Meta: nothing says who the
    ad is for, so it is refused naming both fields rather than sent with a guess."""
    r = await ready(client_for, "ads-dsa-none")
    async with client_for(r.tenant.host) as c:
        assets = (
            await c.get(f"{BASE}/assets", params={"active_only": False}, headers=r.headers)
        ).json()["items"]
        own = next(a for a in assets if a["meta_id"] == AD_ACCOUNT_OWN)
        await c.patch(f"{BASE}/assets/{own['id']}", json={"active": True}, headers=r.headers)
        res = await c.post(
            f"{ADS}/accounts/{own['id']}/campaigns",
            json={"name": "Eigen", "objective": "OUTCOME_TRAFFIC"},
            headers=r.headers,
        )
        campaign = res.json()
        res = await c.post(
            f"{ADS}/accounts/{own['id']}/adsets",
            json={
                "campaign_meta_id": campaign["meta_id"],
                "name": "NL",
                "optimization_goal": "LINK_CLICKS",
                "daily_budget_cents": 1_000,
                "targeting": TARGETING,
            },
            headers=r.headers,
        )
        assert res.status_code == 422
        body = res.json()["error"]
        assert body["code"] == "meta_ads_dsa_required"
        assert set(body["fields"]) == {"dsa_beneficiary", "dsa_payor"}
        assert body["details"] == {"countries": ["NL"]}
        assert fake.adsets == {}

        # Outside the EU nobody asks.
        res = await c.post(
            f"{ADS}/accounts/{own['id']}/adsets",
            json={
                "campaign_meta_id": campaign["meta_id"],
                "name": "VS",
                "optimization_goal": "LINK_CLICKS",
                "daily_budget_cents": 1_000,
                "targeting": {"countries": ["US"]},
            },
            headers=r.headers,
        )
        assert res.status_code == 201


async def test_a_political_campaign_in_the_eu_is_refused_before_the_call(client_for, fake) -> None:
    r = await ready(client_for, "ads-political")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns",
            json={
                "name": "Verkiezingen",
                "objective": "OUTCOME_AWARENESS",
                "special_ad_categories": ["ISSUES_ELECTIONS_POLITICS"],
                "special_ad_category_countries": ["NL", "US"],
            },
            headers=r.headers,
        )
    assert res.status_code == 422
    assert res.json()["error"]["details"] == {"countries": ["NL"]}
    assert not fake.writes("/campaigns")


async def test_metas_own_refusal_names_the_field_it_blamed(client_for, fake) -> None:
    """An agent told "invalid parameter" cannot correct itself. What rides ``details`` is the
    field Meta blamed and its code — identifiers, never Meta's prose (§9)."""
    r = await ready(client_for, "ads-blame")
    async with client_for(r.tenant.host) as c:
        campaign = await _campaign(c, r)
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/adsets",
            json={
                "campaign_meta_id": campaign["meta_id"],
                "name": "Te laag",
                "optimization_goal": "LINK_CLICKS",
                "daily_budget_cents": 50,
                "targeting": TARGETING,
            },
            headers=r.headers,
        )
    assert res.status_code == 422
    body = res.json()["error"]
    assert body["code"] == "meta_invalid"
    assert body["details"]["meta_code"] == 1885272
    assert body["details"]["meta_fields"] == ["daily_budget"]
    assert "te laag" not in res.text.lower()


async def test_a_creative_runs_as_the_same_clients_page(client_for, fake) -> None:
    r = await ready(client_for, "ads-creative")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/creatives",
            json={
                "name": "Herfst",
                "page_id": r.page_id,
                "instagram_id": r.instagram_id,
                "link": "https://novafietsen.example/herfst",
                "message": "Nieuwe collectie",
                "headline": "Herfst 2026",
                "call_to_action": "SHOP_NOW",
                "image_url": "https://cdn.example/herfst.jpg",
            },
            headers=r.headers,
        )
        assert res.status_code == 201, res.text
        spec = fake.writes("/adcreatives")[0].data["object_story_spec"]
        assert f'"page_id":"{PAGE_CLIENT}"' in spec
        assert '"instagram_user_id"' in spec
        assert "instagram_actor_id" not in spec

        # The agency's own Page is not this client's, so an ad for the client cannot run as it.
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/creatives",
            json={"name": "Fout", "page_id": r.own_page_id, "link": "https://x.example"},
            headers=r.headers,
        )
    assert res.status_code == 422
    assert res.json()["error"]["fields"] == {"page_id": "errors.meta_ads_asset_mismatch"}


async def test_a_banned_phrase_stops_a_creative(client_for, fake) -> None:
    r = await ready(client_for, "ads-phrase")
    async with client_for(r.tenant.host) as c:
        await c.put(f"{ADS}/policy", json={"banned_phrases": ["Gratis"]}, headers=r.headers)
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/creatives",
            json={
                "name": "x",
                "page_id": r.page_id,
                "link": "https://x.example",
                "headline": "Nu gratis proefrit",
            },
            headers=r.headers,
        )
    assert res.status_code == 422
    body = res.json()["error"]
    assert body["fields"] == {"headline": "errors.meta_ads_banned_phrase"}
    assert body["details"] == {"phrase": "Gratis"}
    assert not fake.writes("/adcreatives")


async def test_an_image_travels_as_json(client_for, fake) -> None:
    r = await ready(client_for, "ads-image")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/images",
            json={
                "filename": "herfst.png",
                "content_base64": base64.b64encode(image(600, 600)).decode(),
            },
            headers=r.headers,
        )
        bad = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/images",
            json={"filename": "x.png", "content_base64": "not base64!"},
            headers=r.headers,
        )
    assert res.status_code == 201, res.text
    assert res.json()["hash"]
    assert fake.writes("/adimages")[0].files == ["filename"]
    assert bad.status_code == 422


# --- changing ------------------------------------------------------------------------------- #


async def test_switching_on_is_a_permission_of_its_own(client_for, fake) -> None:
    """A key that may build a whole campaign and cannot spend a cent."""
    r = await ready(client_for, "ads-activate")
    async with client_for(r.tenant.host) as c:
        # Give a member everything *except* the right to switch on.
        roles = (await c.get("/api/v1/roles", headers=r.headers)).json()
        rows = roles["items"] if isinstance(roles, dict) else roles
        role = next(x for x in rows if x["key"] == "member")
        granted = sorted(
            {
                *role["permissions"],
                "meta_ads.campaign.write",
                "meta_ads.budget.write",
            }
        )
        res = await c.patch(
            f"/api/v1/roles/{role['id']}", json={"permissions": granted}, headers=r.headers
        )
        assert res.status_code == 200, res.text
        builder = await member(r.tenant, "bouwer@ads-activate.example.com")

        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns",
            json={"name": "Door de bouwer", "objective": "OUTCOME_TRAFFIC"},
            headers=builder,
        )
        assert res.status_code == 201, res.text
        campaign = res.json()["meta_id"]
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns/{campaign}/activate",
            json={},
            headers=builder,
        )
        assert res.status_code == 403
        assert fake.campaigns[campaign]["status"] == "PAUSED"

        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns/{campaign}/activate",
            json={"reason": "Akkoord van de klant"},
            headers=r.headers,
        )
        assert res.status_code == 200, res.text
        assert res.json()["status"] == "ACTIVE"
        assert fake.campaigns[campaign]["status"] == "ACTIVE"

        # Stopping spend is the safe direction, and the builder may do it.
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns/{campaign}/pause",
            json={},
            headers=builder,
        )
        assert res.status_code == 200
        assert fake.campaigns[campaign]["status"] == "PAUSED"


async def test_a_budget_is_judged_before_it_is_sent(client_for, fake) -> None:
    r = await ready(client_for, "ads-budget")
    async with client_for(r.tenant.host) as c:
        campaign = await _campaign(c, r)
        adset = (await _adset(c, r, campaign["meta_id"]))["meta_id"]
        url = f"{ADS}/accounts/{r.ad_account_id}/adsets/{adset}/budget"

        # The extra zero: €25 → €250 is more than double.
        res = await c.put(url, json={"daily_budget_cents": 25_000}, headers=r.headers)
        assert res.status_code == 422
        body = res.json()["error"]
        assert body["code"] == "meta_ads_budget_increase_too_large"
        assert body["fields"] == {"daily_budget_cents": body["message"]}
        assert body["details"] == {"limit": 5_000, "value": 25_000, "previous": 2_500}
        assert fake.adsets[adset]["daily_budget"] == "2500"

        res = await c.put(
            url,
            json={"daily_budget_cents": 4_000, "reason": "Klant wil meer bereik"},
            headers=r.headers,
        )
        assert res.status_code == 200, res.text
        assert fake.adsets[adset]["daily_budget"] == "4000"

        # An absolute ceiling, once somebody states one.
        await c.put(
            f"{ADS}/accounts/{r.ad_account_id}/policy",
            json={"max_daily_budget_cents": 4_500},
            headers=r.headers,
        )
        res = await c.put(url, json={"daily_budget_cents": 5_000}, headers=r.headers)
        assert res.json()["error"]["code"] == "meta_ads_budget_over_ceiling"
        assert res.json()["error"]["details"] == {"limit": 4_500, "value": 5_000}

        both = await c.put(
            url,
            json={"daily_budget_cents": 1_000, "lifetime_budget_cents": 9_000},
            headers=r.headers,
        )
        assert both.status_code == 422

        decisions = (
            await c.get(
                f"{ADS}/accounts/{r.ad_account_id}/decisions",
                params={"subject_meta_id": adset},
                headers=r.headers,
            )
        ).json()
    changed = [d for d in decisions["items"] if d["decision"] == "budget_changed"]
    assert len(changed) == 1
    assert changed[0]["payload"] == {"budget": "daily", "from_cents": 2_500, "to_cents": 4_000}
    assert changed[0]["reason"] == "Klant wil meer bereik"
    assert changed[0]["decided_by_name"] == r.tenant.user.email


async def test_the_fifth_budget_change_in_an_hour_is_refused_with_the_number(
    client_for, fake
) -> None:
    r = await ready(client_for, "ads-budget-rate")
    async with client_for(r.tenant.host) as c:
        campaign = await _campaign(c, r)
        adset = (await _adset(c, r, campaign["meta_id"]))["meta_id"]
        url = f"{ADS}/accounts/{r.ad_account_id}/adsets/{adset}/budget"
        for cents in (2_600, 2_700, 2_800, 2_900):
            res = await c.put(url, json={"daily_budget_cents": cents}, headers=r.headers)
            assert res.status_code == 200, res.text
        res = await c.put(url, json={"daily_budget_cents": 3_000}, headers=r.headers)
    assert res.status_code == 429
    assert res.json()["error"]["details"] == {"limit": 4, "per": "hour"}
    assert fake.adsets[adset]["daily_budget"] == "2900"


async def test_an_id_from_another_account_is_a_404_and_never_a_call(client_for, fake) -> None:
    """The safety property is the lookup: the id arrives from outside, so it is resolved
    inside the account named in the path before anything is sent to it."""
    r = await ready(client_for, "ads-foreign")
    fake.campaigns["990001"] = {
        "id": "990001",
        "account_id": AD_ACCOUNT_OWN,
        "name": "Van een ander",
        "status": "PAUSED",
        "daily_budget": "1000",
    }
    async with client_for(r.tenant.host) as c:
        base = f"{ADS}/accounts/{r.ad_account_id}/campaigns/990001"
        for method, url, body in (
            ("patch", base, {"name": "Gekaapt"}),
            ("put", f"{base}/budget", {"daily_budget_cents": 1_500}),
            ("post", f"{base}/activate", {}),
            ("post", f"{base}/pause", {}),
        ):
            res = await getattr(c, method)(url, json=body, headers=r.headers)
            assert res.status_code == 404, (method, url, res.text)
        res = await c.patch(
            f"{ADS}/accounts/{r.ad_account_id}/campaigns/not-an-id",
            json={"name": "x"},
            headers=r.headers,
        )
        assert res.status_code == 404
    assert fake.campaigns["990001"]["name"] == "Van een ander"
    assert not [w for w in fake.writes("990001")]


async def test_the_kill_switch_stops_every_write_and_no_read(client_for, fake) -> None:
    r = await ready(client_for, "ads-killswitch")
    async with client_for(r.tenant.host) as c:
        campaign = await _campaign(c, r)
        await c.put(f"{ADS}/settings", json={"writes_enabled": False}, headers=r.headers)
        before = len(fake.writes())
        base = f"{ADS}/accounts/{r.ad_account_id}"
        for method, url, body in (
            ("post", f"{base}/campaigns", {"name": "x", "objective": "OUTCOME_TRAFFIC"}),
            ("patch", f"{base}/campaigns/{campaign['meta_id']}", {"name": "y"}),
            ("post", f"{base}/campaigns/{campaign['meta_id']}/activate", {}),
        ):
            res = await getattr(c, method)(url, json=body, headers=r.headers)
            assert res.status_code == 409, res.text
            assert res.json()["error"]["code"] == "meta_ads_writes_disabled"
        assert len(fake.writes()) == before
        assert (await c.get(f"{base}/campaigns", headers=r.headers)).status_code == 200
        # Publishing posts has its own switch and is not touched by this one.
        assert (await c.get(f"{BASE}/settings", headers=r.headers)).json()["writes_enabled"]


async def test_a_post_is_boosted_in_four_paused_steps(client_for, fake) -> None:
    r = await ready(client_for, "ads-boost")
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/boost",
            json={
                "meta_post_id": f"{PAGE_CLIENT}_123456",
                "page_id": r.page_id,
                "daily_budget_cents": 1_000,
                "targeting": TARGETING,
                "reason": "Bericht loopt goed",
            },
            headers=r.headers,
        )
        assert res.status_code == 201, res.text
        body = res.json()
        decisions = (
            await c.get(f"{ADS}/accounts/{r.ad_account_id}/decisions", headers=r.headers)
        ).json()
    assert body["applied"] is True and body["failed_step"] is None
    assert all(body[k] for k in ("campaign_meta_id", "adset_meta_id", "creative_meta_id"))
    assert fake.campaigns[body["campaign_meta_id"]]["status"] == "PAUSED"
    assert fake.adsets[body["adset_meta_id"]]["status"] == "PAUSED"
    assert fake.ads[body["ad_meta_id"]]["status"] == "PAUSED"
    creative = fake.creatives[body["creative_meta_id"]]
    assert creative["object_story_id"] == f"{PAGE_CLIENT}_123456"
    assert decisions["total"] == 4


async def test_a_boost_that_stops_half_way_says_where(client_for, fake) -> None:
    """Meta has no transaction. What was made is named, and all of it is paused."""
    r = await ready(client_for, "ads-boost-partial")
    fake.fail_next("POST", "/adcreatives", error(100, "Invalid parameter", status=400))
    async with client_for(r.tenant.host) as c:
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/boost",
            json={
                "meta_post_id": f"{PAGE_CLIENT}_123456",
                "page_id": r.page_id,
                "daily_budget_cents": 1_000,
                "targeting": TARGETING,
            },
            headers=r.headers,
        )
    body = res.json()
    assert res.status_code == 201
    assert body["failed_step"] == "creative"
    assert body["campaign_meta_id"] and body["adset_meta_id"]
    assert body["ad_meta_id"] is None
    assert fake.campaigns[body["campaign_meta_id"]]["status"] == "PAUSED"


async def test_a_judgement_that_changed_nothing_is_still_written_down(client_for, fake) -> None:
    """The entry only this table can hold: looked at it, left it running (#318)."""
    r = await ready(client_for, "ads-kept")
    async with client_for(r.tenant.host) as c:
        campaign = await _campaign(c, r)
        before = len(fake.writes())
        res = await c.post(
            f"{ADS}/accounts/{r.ad_account_id}/decisions",
            json={
                "subject_type": "campaign",
                "subject_meta_id": campaign["meta_id"],
                "reason": "CPC is hoog, maar de leads zijn goed. Laten lopen.",
            },
            headers=r.headers,
        )
    assert res.status_code == 201, res.text
    body = res.json()
    assert body["decision"] == "kept"
    assert body["applied"] is False
    assert body["subject_name"] == "Najaarscampagne"
    assert len(fake.writes()) == before


# --- policy --------------------------------------------------------------------------------- #


async def test_an_accounts_policy_is_a_diff_over_the_house(client_for, fake) -> None:
    r = await ready(client_for, "ads-policy")
    async with client_for(r.tenant.host) as c:
        house = await c.put(
            f"{ADS}/policy",
            json={
                "max_daily_budget_cents": 10_000,
                "banned_phrases": ["gratis", "Gratis"],
                "steering": "Wij adverteren niet op prijs.",
            },
            headers=r.headers,
        )
        assert house.status_code == 200, house.text
        assert house.json()["own"]["banned_phrases"] == ["gratis"]
        own = await c.put(
            f"{ADS}/accounts/{r.ad_account_id}/policy",
            json={"max_daily_budget_cents": 3_000, "banned_phrases": ["korting"]},
            headers=r.headers,
        )
        body = own.json()
        assert body["own"]["max_daily_budget_cents"] == 3_000
        assert body["effective"]["max_daily_budget_cents"] == 3_000
        assert body["effective"]["banned_phrases"] == ["gratis", "korting"]
        assert body["effective"]["max_budget_increase_pct"] == 100
        assert body["house_steering"] == "Wij adverteren niet op prijs."

        # ``null`` makes a limit inherit again; it never means "no limit".
        again = await c.put(
            f"{ADS}/accounts/{r.ad_account_id}/policy",
            json={"max_daily_budget_cents": None},
            headers=r.headers,
        )
        assert again.json()["own"]["max_daily_budget_cents"] is None
        assert again.json()["effective"]["max_daily_budget_cents"] == 10_000

        res = await c.delete(f"{ADS}/accounts/{r.ad_account_id}/policy", headers=r.headers)
        assert res.status_code == 204
        gone = (await c.get(f"{ADS}/accounts/{r.ad_account_id}/policy", headers=r.headers)).json()
        assert gone["effective"]["banned_phrases"] == ["gratis"]


# --- isolation ------------------------------------------------------------------------------ #


async def test_a_member_reads_and_changes_nothing(client_for, fake) -> None:
    r = await ready(client_for, "ads-member")
    reader = await member(r.tenant, "lezer@ads-member.example.com")
    async with client_for(r.tenant.host) as c:
        base = f"{ADS}/accounts/{r.ad_account_id}"
        assert (await c.get(f"{ADS}/accounts", headers=reader)).status_code == 200
        assert (await c.get(f"{base}/campaigns", headers=reader)).status_code == 200
        for method, url, body in (
            ("post", f"{base}/campaigns", {"name": "x", "objective": "OUTCOME_TRAFFIC"}),
            ("put", f"{ADS}/policy", {"max_daily_budget_cents": 1}),
            ("put", f"{ADS}/settings", {"writes_enabled": False}),
        ):
            res = await getattr(c, method)(url, json=body, headers=reader)
            assert res.status_code == 403, url
    client = await member(r.tenant, "klant@ads-member.example.com", role="client")
    async with client_for(r.tenant.host) as c:
        assert (await c.get(f"{ADS}/accounts", headers=client)).status_code == 403


async def test_one_tenant_never_sees_anothers_ads(client_for, fake) -> None:
    a = await ready(client_for, "ads-iso-a")
    async with client_for(a.tenant.host) as c:
        await _campaign(c, a)
        await c.put(f"{ADS}/policy", json={"max_daily_budget_cents": 1_000}, headers=a.headers)
    b = await make_tenant("ads-iso-b")
    headers = await auth_cookie(b.user)
    async with client_for(b.host) as c:
        assert (await c.get(f"{ADS}/accounts", headers=headers)).json() == []
        house = (await c.get(f"{ADS}/policy", headers=headers)).json()
        assert house["own"]["max_daily_budget_cents"] is None
        base = f"{ADS}/accounts/{a.ad_account_id}"
        for path in ("", "/campaigns", "/insights", "/decisions", "/policy"):
            assert (await c.get(f"{base}{path}", headers=headers)).status_code == 404, path
        res = await c.post(
            f"{base}/campaigns",
            json={"name": "x", "objective": "OUTCOME_TRAFFIC"},
            headers=headers,
        )
        assert res.status_code == 404
