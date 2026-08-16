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
