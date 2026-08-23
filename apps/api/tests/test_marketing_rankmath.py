"""The Rank Math AI Visibility adapter and its link rules (docs/WORDPRESS.md).

Two halves. The unit half drives the adapter against the payload shapes the plugin actually
produces (read out of ``seo-by-rank-math`` 1.0.275, not remembered), because every mistake
available here fails *silently* — as a flat chart, an empty picker, or a score summed into
four figures. The API half proves the link rules a site-key source needs: a website, and a
credential on it.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.integrations.wordpress import client as wp_client
from app.modules.marketing.sources.base import (
    AVERAGED_METRICS,
    LOWER_IS_BETTER,
    METRICS_BY_SOURCE,
)
from app.modules.marketing.sources.rankmath import RankMathAdapter
from tests.conftest import auth_cookie, make_tenant
from tests.wordpress_fake import FakeWordPress

ADAPTER = RankMathAdapter()
PASSWORD = "abcd EFGH ijkl MNOP qrst UVWX"

#: One ``/overview`` response, in the envelope ``Base_Controller::success()`` produces.
_OVERVIEW = {
    "success": True,
    "data": {
        "summary": {"tracked_brands": 2},
        "brands": [
            {
                "id": "brand-1",
                "name": "Klant BV",
                "url": "https://klant.nl",
                "locale": "NL",
                "status": "active",
                "score": 42.5,
                "rank": 3,
                # 0-100, not a −1…1 ratio — the plugin's own badge prints `${round(score)}%`
                # (docs/WORDPRESS.md §3). A fixture on the wrong scale is a fake that agrees
                # with whatever the formatter happens to assume.
                "avg_sentiment": 62.0,
                "mentions": 18,
                "citations": 7,
                "last_analyzed": "2026-08-10T04:00:00Z",
                "analysis_status": "success",
            },
            {
                "id": "brand-2",
                "name": "Ander merk",
                "url": "https://ander.nl",
                "status": "active",
                # Mid-analysis: nulls where numbers will be.
                "score": None,
                "rank": None,
                "avg_sentiment": None,
                "mentions": None,
                "citations": None,
                "analysis_status": "processing",
                "last_analyzed": None,
            },
        ],
    },
}


class _Client:
    """Just enough WordPress client to drive the adapter, and it counts its refreshes."""

    def __init__(self, overview: object = _OVERVIEW, **extra: object) -> None:
        self._overview = overview
        self._extra = extra
        self.refresh_calls = 0
        self.calls: list[str] = []

    async def ai_visibility_overview(self, *, refresh: bool = False) -> dict:
        self.calls.append("overview")
        if refresh:
            self.refresh_calls += 1
        return self._overview  # type: ignore[return-value]

    async def ai_visibility_insights(self, brand_id: str) -> dict:
        self.calls.append(f"insights:{brand_id}")
        return self._extra.get("insights", {})  # type: ignore[return-value]

    async def ai_visibility_queries(self, brand_id: str) -> dict:
        self.calls.append(f"queries:{brand_id}")
        return self._extra.get("queries", {})  # type: ignore[return-value]


# ---------------------------------------------------------------- the metric vocabulary


def test_every_rank_math_metric_is_averaged_never_summed() -> None:
    """A month of snapshots summed is a four-figure "visibility score".

    ``mentions`` and ``citations`` are the subtle ones: they *look* like counts and are not.
    Rank Math reports a brand's running totals as of its last analysis, so two consecutive
    daily snapshots of "18 mentions" mean eighteen, not thirty-six.
    """
    for metric in METRICS_BY_SOURCE["rankmath"]:
        assert metric in AVERAGED_METRICS, f"{metric} would be summed over a period"


def test_a_worse_rank_is_a_higher_number() -> None:
    assert "brand_rank" in LOWER_IS_BETTER
    assert "ai_visibility_score" not in LOWER_IS_BETTER


# ---------------------------------------------------------------- the adapter


async def test_the_picker_lists_the_brands_on_this_site() -> None:
    options = await ADAPTER.list_accounts(_Client())
    assert [option.external_id for option in options] == ["brand-2", "brand-1"]  # by name
    assert options[1].display_name == "Klant BV"
    assert options[1].config["url"] == "https://klant.nl"


async def test_the_picker_does_not_force_an_upstream_analysis() -> None:
    """Choosing which brand to attach must not spend a client's Content AI quota."""
    client = _Client()
    await ADAPTER.list_accounts(client)
    assert client.refresh_calls == 0


async def test_the_sync_forces_a_fresh_upstream_fetch() -> None:
    """**The load-bearing assertion of this file.**

    Rank Math's controller serves a 12-hour ``wp_options`` cache unless forced, and the
    *ability* cannot force it at all. Without ``refresh=1`` schakl would store a number that
    moves only when somebody opens the WordPress dashboard — a chart of when a human last
    logged in, drawn as a chart of a client's AI visibility.
    """
    client = _Client()
    await ADAPTER.fetch_daily(client, "brand-1", date(2026, 8, 1), date(2026, 8, 11), {})
    assert client.refresh_calls == 1


async def test_one_snapshot_stamped_at_the_end_of_the_window() -> None:
    """There is no history upstream, so a range yields one row, not a filled-in range.

    Answering thirty identical rows would be a flat line that looks like measurement.
    """
    daily = await ADAPTER.fetch_daily(
        _Client(), "brand-1", date(2026, 8, 1), date(2026, 8, 11), {}
    )
    assert len(daily) == 1
    assert daily[0].day == date(2026, 8, 11)
    assert daily[0].metrics["ai_visibility_score"] == 42.5
    assert daily[0].metrics["mentions"] == 18
    assert daily[0].metrics["brand_rank"] == 3
    # Carried so a report can say what it compares, rather than announcing a 0% week between
    # two snapshots of the same analysis (#312).
    assert daily[0].metrics["last_analyzed"] == "2026-08-10T04:00:00Z"


async def test_a_brand_with_no_analysis_yet_stores_nothing() -> None:
    """Not a row of zeroes. A zero would draw a line to the floor and back — a visible, wrong
    claim about a client's visibility, made out of a value nobody reported."""
    daily = await ADAPTER.fetch_daily(
        _Client(), "brand-2", date(2026, 8, 1), date(2026, 8, 11), {}
    )
    assert daily == []


async def test_a_brand_that_is_no_longer_tracked_stores_nothing() -> None:
    daily = await ADAPTER.fetch_daily(
        _Client(), "brand-gone", date(2026, 8, 1), date(2026, 8, 11), {}
    )
    assert daily == []


@pytest.mark.parametrize(
    "payload",
    [
        _OVERVIEW,
        # A bare payload, no `{success, data}` wrapper.
        _OVERVIEW["data"],
        # The raw upstream shape the controller maps from, one level deeper.
        {"success": True, "data": {"brands": {"data": _OVERVIEW["data"]["brands"]}}},
    ],
)
async def test_the_envelope_is_read_defensively(payload: object) -> None:
    """Reading the wrapper wrong turns a good payload into "this client has no brands", which
    is indistinguishable from the truth on a screen."""
    options = await ADAPTER.list_accounts(_Client(payload))
    assert {option.external_id for option in options} == {"brand-1", "brand-2"}


async def test_a_garbage_payload_is_no_brands_not_a_crash() -> None:
    for payload in (None, [], "nope", {"data": None}, {"data": {"brands": "x"}}):
        assert await ADAPTER.list_accounts(_Client(payload)) == []


async def test_the_competitor_drilldown() -> None:
    client = _Client(
        insights={
            "competitors": [
                {"name": "Concurrent", "url": "https://c.nl", "mentions": 9, "avg_sentiment": 40.0},
                {"mentions": 1},  # no name — unusable, dropped
            ]
        }
    )
    table = await ADAPTER.drilldown(
        client, "brand-1", "competitors", date(2026, 8, 1), date(2026, 8, 11), {}
    )
    assert table.kind == "competitors"
    assert [row.label for row in table.rows] == ["Concurrent"]
    assert table.rows[0].metrics["mentions"] == 9


async def test_the_prompt_drilldown() -> None:
    client = _Client(
        queries={"queries": [{"text": "beste zonnepanelen", "enabled": True}, {"enabled": True}]}
    )
    table = await ADAPTER.drilldown(
        client, "brand-1", "queries", date(2026, 8, 1), date(2026, 8, 11), {}
    )
    assert [row.label for row in table.rows] == ["beste zonnepanelen"]


def test_the_deep_link_is_built_from_the_brand_url_not_the_credential() -> None:
    """The adapter never sees ``base_url`` — the client carries the password and the adapter
    carries the data, which is the whole point of the seam."""
    assert ADAPTER.deep_link("brand-1", {"url": "https://klant.nl/"}).startswith(
        "https://klant.nl/wp-admin/"
    )
    assert ADAPTER.deep_link("brand-1", {}).startswith("/wp-admin/")


# ---------------------------------------------------------------- the link rules


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value


@pytest.fixture
def wp(monkeypatch) -> FakeWordPress:
    fake = FakeWordPress()
    wp_client.set_transport(fake.transport())
    # The picker caches its option list; the suite has no Redis (test_marketing_api.py does the
    # same). Held on the fixture so a test can assert what was — and was not — cached.
    fake.redis = _FakeRedis()  # type: ignore[attr-defined]
    monkeypatch.setattr(
        "app.modules.marketing.service.get_redis", lambda: fake.redis  # type: ignore[attr-defined]
    )
    yield fake
    wp_client.set_transport(None)


async def _company_website(c, headers) -> tuple[dict, dict]:
    company = (
        await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
    ).json()
    domain = (
        await c.post(
            "/api/v1/domains",
            json={"name": "klant.nl", "company_id": company["id"]},
            headers=headers,
        )
    ).json()
    website = (
        await c.post(
            "/api/v1/websites",
            json={"domain_id": domain["id"], "root": True},
            headers=headers,
        )
    ).json()
    return company, website


async def test_a_rankmath_link_needs_a_website(client_for, wp) -> None:
    """The credential is per website, so a client-level link has nothing to sync with. Refused
    up front rather than discovered as a ``last_error`` on the first nightly run."""
    t = await make_tenant("rm-no-site")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, _ = await _company_website(c, headers)
        response = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "source": "rankmath",
                "external_id": "brand-1",
                "display_name": "Klant BV",
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["error"]["message"] == (
            "errors.marketing_rankmath_website_required"
        )


async def test_a_rankmath_link_needs_a_connected_website(client_for, wp) -> None:
    t = await make_tenant("rm-no-cred")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, website = await _company_website(c, headers)
        response = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "website_id": website["id"],
                "source": "rankmath",
                "external_id": "brand-1",
                "display_name": "Klant BV",
            },
            headers=headers,
        )
        assert response.status_code == 422
        assert response.json()["error"]["message"] == "errors.marketing_rankmath_not_connected"


async def test_a_connected_website_can_be_linked_and_its_brands_listed(client_for, wp) -> None:
    t = await make_tenant("rm-linked")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company, website = await _company_website(c, headers)
        assert (
            await c.post(
                "/api/v1/wordpress/sites",
                json={
                    "website_id": website["id"],
                    "base_url": "https://klant.nl",
                    "username": "agency",
                    "app_password": PASSWORD,
                },
                headers=headers,
            )
        ).status_code == 201

        # The picker resolves the credential through the core seam and lists that site's brands.
        accounts = (
            await c.get(
                f"/api/v1/marketing/accounts?source=rankmath&website_id={website['id']}",
                headers=headers,
            )
        ).json()
        assert accounts["configured"] is not False
        assert [a["external_id"] for a in accounts["accounts"]] == ["brand-1"]

        created = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "website_id": website["id"],
                "source": "rankmath",
                "external_id": "brand-1",
                "display_name": "Klant BV",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text


async def test_the_picker_without_a_website_teaches_rather_than_erroring(client_for, wp) -> None:
    """A site-key source with no website named cannot answer, and ``configured=False`` is the
    state the picker already draws for "no credential yet" — never a 500 and never an empty
    list, which reads as "this client has no brands"."""
    t = await make_tenant("rm-picker")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        accounts = (
            await c.get("/api/v1/marketing/accounts?source=rankmath", headers=headers)
        ).json()
        assert accounts["configured"] is False
        assert accounts["accounts"] == []
        # And nothing was cached: "not configured" is the answer on every page load forever,
        # not a miss worth storing — the same reason the Google path never caches a failure.
        assert wp.redis.store == {}


# --- the guided setup (#435) ------------------------------------------------------------- #
#
# ``configured`` was one boolean over four prerequisites that live in two other products, so
# "there is no credential" and "the credential was refused" answered identically, and "Rank Math
# is not installed" and "this client has no brand yet" were both an empty list. These pin the
# stage each state actually produces — the picker draws a different sentence and a different
# button per stage, so a stage that regresses is a screen that sends somebody to the wrong
# screen, which is exactly the failure that reads as "it just says no accounts".


async def _connected(c, headers, website) -> None:
    assert (
        await c.post(
            "/api/v1/wordpress/sites",
            json={
                "website_id": website["id"],
                "base_url": "https://klant.nl",
                "username": "agency",
                "app_password": PASSWORD,
            },
            headers=headers,
        )
    ).status_code == 201


async def _accounts(c, headers, website, *, refresh: bool = False) -> dict:
    query = f"source=rankmath&website_id={website['id']}"
    if refresh:
        query += "&refresh=1"
    return (await c.get(f"/api/v1/marketing/accounts?{query}", headers=headers)).json()


async def test_a_website_with_no_credential_names_the_stage(client_for, wp) -> None:
    t = await make_tenant("rm-stage-none")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        accounts = await _accounts(c, headers, website)
        assert accounts["setup_stage"] == "no_credential"
        assert accounts["configured"] is False
        # No row, so no site address, so no link — a control built out of a guess is one that
        # can only refuse (#253).
        assert accounts["setup_links"] == {}


async def test_a_refused_credential_is_not_a_missing_one(client_for, wp) -> None:
    """The bug this issue is named after. Both were ``configured=False``, so the picker drew
    *"deze website heeft nog geen WordPress-koppeling"* over a website that has one — and the
    cure (re-mint the application password) was on no screen anywhere."""
    t = await make_tenant("rm-stage-refused")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        wp.app_password = "somebody rotated it"
        accounts = await _accounts(c, headers, website)
        assert accounts["setup_stage"] == "credential_refused"
        assert accounts["configured"] is False
        # And the key that says so is finally reachable: `_org_key_error` read
        # `exc.response.status_code`, which a WordPress failure does not have, so every refusal
        # fell through to the generic "er ging iets mis".
        assert accounts["error"] == "marketing.rankmath_key_rejected"
        assert accounts["setup_links"]["app_passwords"].startswith("https://klant.nl/wp-admin/")


async def test_an_editors_password_is_a_scope_problem_not_a_wrong_one(client_for, wp) -> None:
    """Every AI Visibility route is ``manage_options``. Re-minting the same account's password
    would fail identically, so this must not read as "the credentials were refused"."""
    t = await make_tenant("rm-stage-editor")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        # Observed by the probe, which is what lets the stage tell this from a bad password:
        # both are a 403 on the same route.
        site_id = (
            await c.get(
                f"/api/v1/wordpress/sites?website_id={website['id']}", headers=headers
            )
        ).json()[0]["id"]
        wp.is_admin = False
        await c.post(f"/api/v1/wordpress/sites/{site_id}/verify", headers=headers)
        accounts = await _accounts(c, headers, website)
        assert accounts["setup_stage"] == "not_administrator"
        # Not an error: a prerequisite nobody has completed is a thing still to do, and a red
        # "er ging iets mis" over a checklist explaining the exact next step is the noise.
        assert accounts["error"] is None


async def test_rank_math_absent_and_too_old_are_different_stages(client_for, wp) -> None:
    t = await make_tenant("rm-stage-plugin")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        site_id = (
            await c.get(
                f"/api/v1/wordpress/sites?website_id={website['id']}", headers=headers
            )
        ).json()[0]["id"]

        wp.rankmath_version = None
        await c.post(f"/api/v1/wordpress/sites/{site_id}/verify", headers=headers)
        assert (await _accounts(c, headers, website))["setup_stage"] == "rankmath_missing"

        # Installed, and older than the release that first shipped AI Visibility. "Install it"
        # and "update it" are two different jobs and only one of them is possible here.
        wp.rankmath_version = "1.0.272"
        await c.post(f"/api/v1/wordpress/sites/{site_id}/verify", headers=headers)
        assert (await _accounts(c, headers, website))["setup_stage"] == "rankmath_too_old"


async def test_an_unsubscribed_rank_math_says_so(client_for, wp) -> None:
    """``aiv_unauthorized`` is Rank Math saying this site's plan does not reach AI Visibility.
    Nothing about the WordPress credential is wrong, and sending somebody to re-mint one is a
    wasted afternoon."""
    t = await make_tenant("rm-stage-plan")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        wp.aiv_subscribed = False
        accounts = await _accounts(c, headers, website)
        assert accounts["setup_stage"] == "ai_visibility_unavailable"
        assert accounts["setup_links"]["ai_visibility"].endswith("page=rank-math-ai-visibility")


async def test_a_working_site_with_no_brands_is_not_an_empty_picker(client_for, wp) -> None:
    """The reported symptom. Rank Math answers, so everything is set up — what is missing is a
    brand, which is a job in Rank Math and was drawn as "Geen accounts beschikbaar"."""
    t = await make_tenant("rm-stage-brands")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        wp.brands = []
        accounts = await _accounts(c, headers, website)
        assert accounts["setup_stage"] == "no_brands"
        # The plumbing *is* configured — that is the whole difference from every stage above,
        # and it is why this one links into Rank Math rather than back to the website page.
        assert accounts["configured"] is True
        assert accounts["accounts"] == []


async def test_a_site_that_is_set_up_reads_ready_and_costs_no_diagnosis(client_for, wp) -> None:
    t = await make_tenant("rm-stage-ready")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        accounts = await _accounts(c, headers, website)
        assert accounts["setup_stage"] == "ready"
        assert [a["external_id"] for a in accounts["accounts"]] == ["brand-1"]
        # A non-empty list is itself the evidence that every prerequisite is met, so the common
        # case pays for no extra read (docs/PERFORMANCE.md).
        assert accounts["setup_links"] == {}


async def test_a_brand_added_a_minute_ago_is_reachable_without_waiting_out_the_cache(
    client_for, wp
) -> None:
    """The "not all of them" half. The option list is cached for ten minutes, so somebody who
    has just created the brand they came here to link was handed a stale list with no control
    that disagreed with it."""
    t = await make_tenant("rm-refresh")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        _, website = await _company_website(c, headers)
        await _connected(c, headers, website)
        assert len((await _accounts(c, headers, website))["accounts"]) == 1

        wp.brands = [
            *wp.brands,
            {"id": "brand-9", "name": "Nieuw merk", "url": "https://nieuw.nl", "score": 10},
        ]
        # Still the cached answer…
        assert len((await _accounts(c, headers, website))["accounts"]) == 1
        # …until asked again.
        fresh = await _accounts(c, headers, website, refresh=True)
        assert [a["external_id"] for a in fresh["accounts"]] == ["brand-1", "brand-9"]
        # And the fresh answer replaces the stale entry rather than bypassing it forever.
        assert len((await _accounts(c, headers, website))["accounts"]) == 2


async def test_a_google_source_has_no_setup_stage(client_for, wp) -> None:
    """The stage is a site-key concept. A source with no per-website setup must not grow one,
    or the picker draws a four-step checklist over a Google connection."""
    t = await make_tenant("rm-stage-ga4")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        accounts = (
            await c.get("/api/v1/marketing/accounts?source=ga4", headers=headers)
        ).json()
        assert accounts["setup_stage"] is None
