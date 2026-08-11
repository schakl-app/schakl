"""The read surface, driven end to end through the fake transport.

Every request here travels the real OAuth client, the real header builder, the real paging loop
and the real error classifier — the fake is installed at the transport, which is the lowest seam
there is. So a header bug, a `pageToken` bug or a misread `errorCode` fails *here* rather than
against a live account.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.crypto import encrypt
from app.core.googleads import set_transport
from app.db import async_session_maker, set_current_org
from app.modules.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.modules.google.oauth import SCOPE_ADS
from app.modules.google_ads.models import GoogleAdsAccount, GoogleAdsSettings
from tests.conftest import auth_cookie, make_tenant
from tests.googleads_fake import (
    FakeGoogleAds,
    campaign_row,
    failure,
    keyword_row,
    metrics,
    search_term_row,
)

pytestmark = pytest.mark.asyncio

CUSTOMER = "1242643293"
MANAGER = "8408804299"


@pytest.fixture
def fake() -> FakeGoogleAds:
    """A Google Ads that answers nothing until a test scripts it, and is torn down after."""
    stub = FakeGoogleAds()
    set_transport(stub.transport())
    try:
        yield stub
    finally:
        set_transport(None)


async def _linked(slug: str):
    """An org with a Google connection, a developer token and one linked Ads account."""
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
        connection = GoogleConnection(
            org_id=t.org.id,
            user_id=t.user.id,
            google_sub="sub-1",
            email="ads@example.com",
            scopes=[SCOPE_ADS],
            refresh_token_encrypted=encrypt("1//fake-refresh-token"),
            status=ConnectionStatus.ACTIVE.value,
        )
        session.add(connection)
        session.add(
            GoogleAdsSettings(
                org_id=t.org.id, developer_token_encrypted=encrypt("fake-developer-token")
            )
        )
        await session.flush()
        account = GoogleAdsAccount(
            org_id=t.org.id,
            customer_id=CUSTOMER,
            login_customer_id=MANAGER,
            connection_id=connection.id,
            descriptive_name="AAZET",
            currency_code="EUR",
            time_zone="Europe/Amsterdam",
        )
        session.add(account)
        await session.commit()
        account_id = account.id
    return t, account_id


# --- the envelope --------------------------------------------------------------------------- #


async def test_a_campaign_read_carries_the_account_the_period_and_the_currency(
    client_for, fake
) -> None:
    t, account_id = await _linked("gads-read-envelope")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        [
            campaign_row(
                1,
                "Merk",
                budget_micros=40_000_000,
                impressions=1000,
                clicks=50,
                cost_micros=25_000_000,
                conversions=5,
            )
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/campaigns?period=30d", headers=headers
        )
    assert res.status_code == 200
    body = res.json()
    assert body["account"]["customer_id_formatted"] == "124-264-3293"
    assert body["currency"] == "EUR"
    assert body["account_timezone"] == "Europe/Amsterdam"
    assert body["period"]["days"] == 30
    assert body["period"]["token"] == "30d"
    assert body["row_count"] == 1


async def test_the_manager_header_rides_every_read(client_for, fake) -> None:
    """Without it every call is made by a login with no direct grant on the client account."""
    t, account_id = await _linked("gads-read-manager")
    headers = await auth_cookie(t.user)
    fake.script("FROM campaign", [campaign_row(1, "Merk")])
    async with client_for(t.host) as c:
        await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    ads_calls = [c for c in fake.calls if c[1].endswith("googleAds:search")]
    assert ads_calls, "no Ads call was made"
    for _method, _url, sent, _body in ads_calls:
        assert sent["login-customer-id"] == MANAGER
        assert sent["developer-token"] == "fake-developer-token"


async def test_money_is_micros_and_ids_stay_strings(client_for, fake) -> None:
    """An Ads id is an int64: above 2^53 a JSON number loses precision, which is why Google
    sends it as a string and why it stays one all the way to the client."""
    t, account_id = await _linked("gads-read-money")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        [campaign_row(9007199254740993, "Grote id", cost_micros=1_234_560_000, clicks=10)],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    row = res.json()["rows"][0]
    assert row["campaign_id"] == "9007199254740993"
    assert row["cost"] == 1234.56


async def test_ratios_are_fractions_and_the_uncomputable_ones_are_null(client_for, fake) -> None:
    """`0` is a measurement; `null` is the absence of one. A cost per conversion of zero would
    be the single most misleading number this API could return."""
    t, account_id = await _linked("gads-read-ratios")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        [
            campaign_row(
                1,
                "Spend, geen conversies",
                impressions=1000,
                clicks=45,
                cost_micros=90_000_000,
                conversions=0,
            ),
            campaign_row(2, "Geen vertoningen", impressions=0, clicks=0, cost_micros=0),
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    spent, silent = res.json()["rows"]
    assert spent["ctr"] == 0.045  # a fraction: 4,5 %
    assert spent["cost_per_conversion"] is None
    assert spent["conversion_rate"] == 0.0  # measured: 45 clicks, no conversions
    assert silent["ctr"] is None  # not computable: no impressions at all
    assert silent["average_cpc"] is None


async def test_totals_recompute_ratios_rather_than_averaging_them(client_for, fake) -> None:
    """The average of two CTRs is not the CTR of the two rows together."""
    t, account_id = await _linked("gads-read-totals")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        [
            campaign_row(
                1, "A", impressions=1000, clicks=100, cost_micros=100_000_000, conversions=10
            ),
            campaign_row(
                2, "B", impressions=9000, clicks=100, cost_micros=100_000_000, conversions=0
            ),
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    totals = res.json()["totals"]
    assert totals["impressions"] == 10_000
    assert totals["clicks"] == 200
    assert totals["ctr"] == 0.02  # 200/10000 — not (0.1 + 0.011)/2
    assert totals["cost_per_conversion"] == 20.0  # 200 / 10


async def test_both_homes_of_the_target_cpa_are_read(client_for, fake) -> None:
    """`target_cpa` lives in two different messages depending on the bidding strategy. Reading
    one is correct for half an agency's campaigns and null for the other half."""
    t, account_id = await _linked("gads-read-bidding")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        [
            campaign_row(1, "Target CPA", target_cpa_micros=25_000_000),
            campaign_row(2, "Maximize conversions", maximize_conversions_cpa_micros=30_000_000),
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    assert [row["target_cpa"] for row in res.json()["rows"]] == [25.0, 30.0]


# --- paging and truncation ------------------------------------------------------------------ #


async def test_paging_follows_the_token_rather_than_returning_a_prefix(client_for, fake) -> None:
    """`pageSize` is ignored by Google — the page is a fixed 10 000 rows and `pageToken` is the
    only way through. A client that ignores it silently returns the first page as the answer."""
    t, account_id = await _linked("gads-read-paging")
    headers = await auth_cookie(t.user)
    rows = [keyword_row(f"woord {i}", criterion_id=i, clicks=i) for i in range(1, 7)]
    fake.script("FROM keyword_view", rows, pages=3)
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/keywords", headers=headers)
    assert res.json()["row_count"] == 6
    assert len([q for q in fake.queries() if "keyword_view" in q]) == 3


async def test_a_truncated_list_says_so(client_for, fake) -> None:
    """A cut nobody is told about is a truncation, and a list of 5 that is really 50 reads as
    an account with 5 (CLAUDE.md §17)."""
    t, account_id = await _linked("gads-read-truncate")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM search_term_view",
        [search_term_row(f"term {i}", clicks=i) for i in range(1, 8)],
    )
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/search-terms?limit=3", headers=headers
        )
    body = res.json()
    assert body["row_count"] == 3
    assert "google_ads.warning.rows_truncated" in body["warnings"]


async def test_search_terms_never_pretend_to_be_classified(client_for, fake) -> None:
    """The API labels nothing as a candidate negative, and an agent must not read the list as a
    verdict."""
    t, account_id = await _linked("gads-read-terms")
    headers = await auth_cookie(t.user)
    fake.script("FROM search_term_view", [search_term_row("gratis offerte", status="NONE")])
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/search-terms", headers=headers)
    body = res.json()
    assert "google_ads.warning.search_terms_unclassified" in body["warnings"]
    assert body["rows"][0]["match_status"] == "NONE"


# --- filters -------------------------------------------------------------------------------- #


async def test_a_campaign_name_never_reaches_the_gaql(client_for, fake) -> None:
    """Names are translated to ids first, so only integers are ever interpolated into a query."""
    t, account_id = await _linked("gads-read-filter")
    headers = await auth_cookie(t.user)
    fake.script(
        "SELECT campaign.id, campaign.name FROM campaign",
        [{"campaign": {"id": "77", "name": "VELUX dakramen"}}],
    )
    fake.script(
        "FROM ad_group",
        [
            {
                "campaign": {"id": "77", "name": "VELUX dakramen"},
                "adGroup": {"id": "1", "name": "A"},
                "metrics": metrics(clicks=1),
            }
        ],
    )
    async with client_for(t.host) as c:
        matched = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/ad-groups?campaigns=velux",
            headers=headers,
        )
    assert matched.status_code == 200
    ad_group_queries = [q for q in fake.queries() if "FROM ad_group" in q]
    assert ad_group_queries
    # The name matched case-insensitively, and what reached the query is an integer.
    assert "campaign.id IN (77)" in ad_group_queries[0]
    assert "velux" not in ad_group_queries[0].casefold()

    # And a name shaped like an injection reaches no query at all: the resolve step is a fixed
    # string, and the only thing that survives it is an id that matched.
    fake.calls.clear()
    async with client_for(t.host) as c:
        await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/ad-groups?campaigns=velux' OR 1=1--",
            headers=headers,
        )
    assert all("OR 1=1" not in query for query in fake.queries())
    assert "campaign.id IN (77)" in ad_group_queries[0]


async def test_a_filter_matching_nothing_returns_nothing(client_for, fake) -> None:
    """`None` (no filter) and `[]` (no match) are different answers, and collapsing them turns a
    typo in a campaign name into a report on the whole account."""
    t, account_id = await _linked("gads-read-nomatch")
    headers = await auth_cookie(t.user)
    fake.script(
        "SELECT campaign.id, campaign.name FROM campaign",
        [{"campaign": {"id": "77", "name": "VELUX"}}],
    )
    fake.script(
        "FROM ad_group",
        [{"adGroup": {"id": "1", "name": "should not appear"}, "metrics": metrics()}],
    )
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/ad-groups?campaigns=bestaat-niet",
            headers=headers,
        )
    body = res.json()
    assert body["rows"] == []
    assert "google_ads.warning.no_campaigns_matched" in body["warnings"]


# --- the reads with their own shapes -------------------------------------------------------- #


async def test_negatives_answer_all_three_levels_at_once(client_for, fake) -> None:
    """Google models exclusions as three resources; an agency asks one question."""
    t, account_id = await _linked("gads-read-negatives")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM ad_group_criterion",
        [
            {
                "campaign": {"id": "1", "name": "Zoeken"},
                "adGroup": {"id": "2", "name": "Merk"},
                "adGroupCriterion": {
                    "criterionId": "9",
                    "keyword": {"text": "gratis", "matchType": "BROAD"},
                },
            }
        ],
    )
    fake.script(
        "FROM campaign_criterion",
        [
            {
                "campaign": {"id": "1", "name": "Zoeken"},
                "campaignCriterion": {
                    "criterionId": "10",
                    "keyword": {"text": "vacature", "matchType": "PHRASE"},
                },
            }
        ],
    )
    fake.script(
        "FROM shared_criterion",
        [
            {
                "sharedSet": {"id": "5", "name": "Uitsluitingen breed"},
                "sharedCriterion": {
                    "criterionId": "11",
                    "keyword": {"text": "tweedehands", "matchType": "EXACT"},
                },
            }
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/negatives", headers=headers)
    body = res.json()
    assert {row["level"] for row in body["rows"]} == {"ad_group", "campaign", "shared_set"}
    shared = next(r for r in body["rows"] if r["level"] == "shared_set")
    assert shared["shared_set_name"] == "Uitsluitingen breed"
    # Configuration, not a measurement: no period, no totals.
    assert body["period"] is None


async def test_geo_falls_back_to_country_and_says_which(client_for, fake) -> None:
    """Some accounts cannot segment below country. Silently returning country rows labelled as
    cities would be the worst outcome; the label is the fix."""
    t, account_id = await _linked("gads-read-geo")
    headers = await auth_cookie(t.user)
    fake.script(
        "segments.geo_target_city",
        failure("queryError", "UNRECOGNIZED_FIELD", message="cannot select city"),
    )
    fake.script(
        "FROM user_location_view",
        [
            {
                "campaign": {"id": "1", "name": "Zoeken"},
                "segments": {"geoTargetCountry": "geoTargetConstants/2528"},
                "userLocationView": {"countryCriterionId": "2528"},
                "metrics": metrics(clicks=5),
            }
        ],
    )
    fake.script(
        "FROM geo_target_constant",
        [{"geoTargetConstant": {"resourceName": "geoTargetConstants/2528", "name": "Netherlands"}}],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/geo", headers=headers)
    body = res.json()
    assert body["extra"]["granularity"] == "country"
    assert "google_ads.warning.geo_country_only" in body["warnings"]
    assert body["rows"][0]["country"] == "Netherlands"
    assert body["rows"][0]["city"] is None


async def test_changes_report_the_window_they_actually_read(client_for, fake) -> None:
    """`change_event` reaches back 30 days and no further; asking for 90 quietly answers less."""
    t, account_id = await _linked("gads-read-changes")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM change_event",
        [
            {
                "changeEvent": {
                    "changeDateTime": "2026-08-01 09:00:00",
                    "changeResourceType": "CAMPAIGN_BUDGET",
                    "resourceChangeOperation": "UPDATE",
                    "changedFields": "amount_micros",
                    "userEmail": "stan@breik.nl",
                    "oldResource": {"campaignBudget": {"amountMicros": "40000000"}},
                    "newResource": {"campaignBudget": {"amountMicros": "400000000"}},
                }
            }
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/changes?period=90d", headers=headers
        )
    body = res.json()
    assert "google_ads.warning.changes_window_shortened" in body["warnings"]
    assert "google_ads.warning.changes_exclude_automation" in body["warnings"]
    effective = body["extra"]["effective_period"]
    assert (
        datetime.fromisoformat(effective["to"]).date()
        - datetime.fromisoformat(effective["from"]).date()
    ) <= timedelta(days=30)
    # The whole point of the read: the old and new value of the field that changed.
    change = body["rows"][0]["changed_fields"][0]
    assert change == {"field": "amount_micros", "from": "40000000", "to": "400000000"}


# --- the query passthrough ------------------------------------------------------------------ #


async def test_the_passthrough_imposes_a_limit_and_reports_what_ran(client_for, fake) -> None:
    t, account_id = await _linked("gads-read-query")
    headers = await auth_cookie(t.user)
    fake.script("FROM campaign", [{"campaign": {"id": "1", "name": "Zoeken"}}])
    async with client_for(t.host) as c:
        res = await c.post(
            f"/api/v1/google-ads/accounts/{account_id}/query",
            json={"query": "SELECT campaign.id, campaign.name FROM campaign"},
            headers=headers,
        )
    body = res.json()
    assert body["resource"] == "campaign"
    assert body["executed_query"].endswith("LIMIT 200")
    assert fake.queries()[-1].endswith("LIMIT 200")


async def test_the_passthrough_refuses_a_resource_outside_the_allow_list(client_for, fake) -> None:
    """And refuses it *before* a client is opened, so no quota is spent finding out."""
    t, account_id = await _linked("gads-read-query-deny")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.post(
            f"/api/v1/google-ads/accounts/{account_id}/query",
            json={"query": "SELECT billing_setup.id FROM billing_setup"},
            headers=headers,
        )
    assert res.status_code == 422
    assert res.json()["error"]["message"] == "errors.google_ads_query_resource_not_allowed"
    assert fake.queries() == []


async def test_the_passthrough_needs_its_own_permission(client_for, fake) -> None:
    """A key scoped to read the account must not be able to ask arbitrary questions of it."""
    t, account_id = await _linked("gads-read-query-perm")
    member = await make_tenant("gads-query-m", email="query-member@example.com")
    from tests.conftest import add_membership

    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        await add_membership(session, t.org.id, member.user.id, role="member")
        await session.commit()
    member_headers = await auth_cookie(member.user, org_id=t.org.id)
    async with client_for(t.host) as c:
        allowed = await c.get(f"/api/v1/google-ads/accounts/{account_id}", headers=member_headers)
        refused = await c.post(
            f"/api/v1/google-ads/accounts/{account_id}/query",
            json={"query": "SELECT campaign.id FROM campaign"},
            headers=member_headers,
        )
    assert allowed.status_code == 200  # a member may read the account
    assert refused.status_code == 403  # …and may not run free-form queries against it


# --- failure surfaces ----------------------------------------------------------------------- #


async def test_a_google_refusal_becomes_an_envelope_not_a_500(client_for, fake) -> None:
    """`AdsError` is an `AppError`, so every route surfaces the right status and i18n key
    without each one remembering to catch anything."""
    t, account_id = await _linked("gads-read-refusal")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        failure("authorizationError", "USER_PERMISSION_DENIED", status=403),
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    assert res.status_code == 409
    assert res.json()["error"]["message"] == "errors.google_ads_permission"


async def test_google_s_own_text_never_reaches_the_envelope(client_for, fake) -> None:
    """§9: the envelope carries an i18n key. Provider prose lives on the account row."""
    t, account_id = await _linked("gads-read-scrub")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM campaign",
        failure(
            "authenticationError",
            "DEVELOPER_TOKEN_INVALID",
            status=401,
            message="Developer token fake-developer-token is not valid.",
        ),
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    assert res.json()["error"]["message"] == "errors.google_ads_developer_token"
    assert "fake-developer-token" not in res.text
    assert "not valid" not in res.text


async def test_an_account_with_no_connection_is_a_presentable_state(client_for, fake) -> None:
    """A dormant link asks to be reconnected; it does not 500 and does not ask Google about a
    customer named "None"."""
    t, account_id = await _linked("gads-read-dormant")
    headers = await auth_cookie(t.user)
    async with async_session_maker() as session:
        await set_current_org(session, t.org.id)
        account = await session.scalar(
            select(GoogleAdsAccount).where(GoogleAdsAccount.id == account_id)
        )
        account.connection_id = None
        await session.commit()
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=headers)
    assert res.status_code == 409
    assert res.json()["error"]["message"] == "errors.google_ads_not_configured"
    assert fake.queries() == []


async def test_a_read_on_another_tenants_account_is_a_404(client_for, fake) -> None:
    t, account_id = await _linked("gads-read-iso-a")
    other = await make_tenant("gads-read-iso-b")
    other_headers = await auth_cookie(other.user)
    async with client_for(other.host) as c:
        res = await c.get(
            f"/api/v1/google-ads/accounts/{account_id}/campaigns", headers=other_headers
        )
    assert res.status_code == 404
    assert fake.queries() == []


# --- the snapshot --------------------------------------------------------------------------- #


async def test_the_snapshot_counts_only_what_is_actually_spending(client_for, fake) -> None:
    """A paused campaign's budget buys nothing, so it is not part of the daily commitment."""
    t, account_id = await _linked("gads-read-snapshot")
    headers = await auth_cookie(t.user)
    fake.script(
        "FROM customer",
        [
            {
                "customer": {
                    "id": CUSTOMER,
                    "descriptiveName": "AAZET",
                    "currencyCode": "EUR",
                    "timeZone": "Europe/Amsterdam",
                    "status": "ENABLED",
                    "conversionTrackingSetting": {
                        "conversionTrackingStatus": "CONVERSION_TRACKING_MANAGED_BY_SELF"
                    },
                },
                "metrics": metrics(clicks=100, cost_micros=500_000_000, conversions=20),
            }
        ],
    )
    fake.script(
        "FROM campaign",
        [
            campaign_row(1, "Actief", status="ENABLED", budget_micros=40_000_000),
            campaign_row(2, "Gepauzeerd", status="PAUSED", budget_micros=100_000_000),
        ],
    )
    async with client_for(t.host) as c:
        res = await c.get(f"/api/v1/google-ads/accounts/{account_id}/snapshot", headers=headers)
    body = res.json()
    assert body["campaign_count"] == 2
    assert body["enabled_campaign_count"] == 1
    assert body["total_daily_budget"] == 40.0
    assert body["account_summary"]["conversion_tracking_status"].startswith("CONVERSION_TRACKING")


async def test_every_read_releases_the_database_connection(client_for, fake) -> None:
    """A request pins one pooled connection for its whole transaction; held across a Google call
    a handful of these drain the pool and the site appears to freeze. This asserts the shape
    rather than the symptom: the account row is loaded, then the connection is handed back."""
    import inspect

    from app.modules.google_ads.service import GoogleAdsService

    source = inspect.getsource(GoogleAdsService.open_client)
    assert "release_db" in source
    # …and it is entered *after* the client, which reads settings off the session.
    assert source.index("ads_client(") < source.index("release_db")
