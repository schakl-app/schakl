"""SE Ranking's AI Search overview (docs/SERANKING.md).

What is pinned here is what fails *silently* if it is wrong:

- **which endpoint "every engine" is** — the aggregate is its own path, not the by-engine path
  with the parameter left off (that one requires ``engine`` and refuses);
- **which month the figures are** — SE Ranking's summary names none, so the month comes off the
  series beside it, and both ways it can differ from the month asked about are handled;
- **that a paid call is made once** — a second read, a second reader and a stored refusal all
  answer from the database, because the call costs the agency 800 units every time;
- **that a refusal names itself** — "no Data API access" and "the plan is out of units" have
  two different people who can fix them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select, update

from app.db import async_session_maker, set_current_org
from app.modules.marketing import service as marketing_service
from app.modules.marketing.aisearch import (
    AiSearchSettings,
    brand_fits,
    change,
    clean_target,
    diff,
    parse,
    parse_overview,
    previous_month,
    resolve,
)
from app.modules.marketing.aisearch.service import expected_month
from app.modules.marketing.models import MarketingAiSearchSnapshot
from app.modules.marketing.sources.seranking import (
    DataApiRefused,
    SeRankingAdapter,
    classify_refusal,
)
from tests.conftest import auth_cookie, make_tenant, org_today

ADAPTER = SeRankingAdapter()


def _body(latest: date, *, months: int = 3) -> dict[str, Any]:
    """An overview body in the documented shape, its series ending at ``latest``."""
    points: list[date] = [latest]
    for _ in range(months - 1):
        points.append(previous_month(points[-1]))
    points.reverse()

    def series(start: float, step: float) -> list[dict[str, Any]]:
        return [
            {"date": month.strftime("%Y-%m"), "value": start + step * index}
            for index, month in enumerate(points)
        ]

    return {
        "summary": {
            "brand_presence": {
                "current": 50, "previous": 45, "change_absolute": 5, "change_percent": 11.1,
            },
            "link_presence": {
                "current": 120, "previous": 110, "change_absolute": 10, "change_percent": 9.1,
            },
            # The vendor's own sign here is "improved by 0.7" — which is why it is not read.
            "average_position": {
                "current": 8.5, "previous": 9.2, "change_absolute": 0.7, "change_percent": 7.6,
            },
            "ai_opportunity_traffic": {
                "current": 2000, "previous": 1800, "change_absolute": 200, "change_percent": 11.1,
            },
        },
        "time_series": {
            "overall_traffic": series(41000, 1000),
            "organic_traffic": series(38000, 1000),
            "ai_traffic": series(2000, 500),
            "link_presence": series(100, 10),
            "average_position": series(9.9, -0.7),
        },
    }


class _Response:
    def __init__(self, payload: object, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status
        self.content = b"x"

    def json(self) -> object:
        return self._payload


class _FakeSeRanking:
    """SE Ranking's Data API, as far as this feature talks to it — and a ledger of the calls."""

    def __init__(self, latest: date) -> None:
        self.latest = latest
        self.units_left: int | None = 50_000
        #: Set to a status code to refuse the paid calls the way the live API does.
        self.refuse: tuple[int, dict[str, Any]] | None = None
        self.brands: list[str] = ["Acme"]
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.keys: list[str] = []
        #: A body to answer the overview with instead of the documented shape — a live one.
        self.body: dict[str, Any] | None = None

    def paid(self) -> list[tuple[str, dict[str, Any]]]:
        return [call for call in self.calls if "/ai-search/overview/" in call[0]]

    async def get(self, url: str, params: dict | None = None) -> _Response:
        self.calls.append((url, dict(params or {})))
        if url.endswith("/account/subscription"):
            if self.refuse and self.refuse[0] in (401, 403):
                return _Response(self.refuse[1], self.refuse[0])
            return _Response(
                {
                    "subscription_info": {
                        "status": "active",
                        "units_limit": 100_000,
                        "units_left": self.units_left,
                        "expiraton_date": "2027-01-01",
                    }
                }
            )
        if url.endswith("/sites"):
            return _Response([])
        if self.refuse:
            return _Response(self.refuse[1], self.refuse[0])
        if url.endswith("/ai-search/discover-brand"):
            return _Response({"brands": self.brands})
        if "/ai-search/overview/" in url:
            return _Response(self.body if self.body is not None else _body(self.latest))
        return _Response({}, 404)


@pytest.fixture
def seranking(monkeypatch: pytest.MonkeyPatch) -> _FakeSeRanking:
    fake = _FakeSeRanking(expected_month(org_today()))

    @asynccontextmanager
    async def fake_client(api_key: str):  # noqa: ANN202
        fake.keys.append(api_key)
        yield fake

    monkeypatch.setattr(marketing_service, "org_key_client", fake_client)
    return fake


# --- the parse, with no database in sight ----------------------------------------------------- #
def test_the_ordinary_answer_is_served_as_the_vendor_gave_it() -> None:
    parsed = parse_overview(_body(date(2026, 8, 1)), date(2026, 8, 1))
    assert parsed.data_month == date(2026, 8, 1)
    assert parsed.realigned is False
    assert parsed.summary["brand_presence"] == {"current": 50.0, "previous": 45.0}
    assert [p["month"] for p in parsed.series["link_presence"]] == ["2026-06", "2026-07", "2026-08"]


def test_a_month_the_vendor_has_not_published_is_labelled_as_the_month_it_is() -> None:
    """Asked about August, answered through July: those are July's figures, and saying so is
    the whole point — relabelling them "augustus" is a number under the wrong heading."""
    parsed = parse_overview(_body(date(2026, 7, 1)), date(2026, 8, 1))
    assert parsed.data_month == date(2026, 7, 1)
    assert parsed.realigned is False
    assert parsed.summary["link_presence"]["current"] == 120.0


def test_a_running_month_is_realigned_to_the_month_that_was_asked_about() -> None:
    """The vendor's newest point is September, still running. Link presence and position are
    re-read from the series for August against July; brand presence and opportunity traffic
    have no series, so August is what the vendor called ``previous`` and there is no July."""
    parsed = parse_overview(_body(date(2026, 9, 1)), date(2026, 8, 1))
    assert parsed.data_month == date(2026, 8, 1)
    assert parsed.realigned is True
    assert parsed.summary["link_presence"] == {"current": 110.0, "previous": 100.0}
    assert parsed.summary["brand_presence"] == {"current": 45.0, "previous": None}
    assert parsed.summary["ai_opportunity_traffic"] == {"current": 1800.0, "previous": None}


def test_a_position_that_fell_is_a_down_arrow_and_good_news() -> None:
    moved = change("average_position", 8.5, 9.2)
    assert (moved["direction"], moved["verdict"]) == ("down", "good")
    assert moved["absolute"] == -0.7
    grew = change("brand_presence", 50, 45)
    assert (grew["direction"], grew["verdict"], grew["percent"]) == ("up", "good", 11.1)
    # No previous value is no comparison — never a change of 100 %.
    assert change("brand_presence", 50, None)["percent"] is None


# --- the shapes the live Data API actually answers (docs/SERANKING.md §10, 2026-09-22) -------- #
def _live(latest: date, *, months: int = 3, summary: dict[str, Any] | None = None) -> dict:
    """An overview body as ``api.seranking.com`` answered it: ``previous`` null on every figure
    (and the vendor's ``change_percent`` a baseline's 100), series ending at ``latest``."""
    body = _body(latest, months=months)
    body["brand"], body["brand_origin"] = "acme", "discovered"
    for key, entry in body["summary"].items():
        entry.update(previous=None, change_percent=100, change_absolute=entry["current"])
        if summary and key in summary:
            entry["current"] = summary[key]
    return body


def test_a_live_answer_takes_the_month_before_from_the_series() -> None:
    """SE Ranking never fills ``previous``. Without the series, no tile would ever compare."""
    parsed = parse_overview(_live(date(2026, 8, 1)), date(2026, 8, 1))
    assert parsed.summary["link_presence"] == {"current": 120.0, "previous": 110.0}
    assert parsed.summary["average_position"]["previous"] == pytest.approx(9.2)
    # No series behind these two: nothing to compare with, and never the vendor's +100 %.
    assert parsed.summary["brand_presence"] == {"current": 50.0, "previous": None}
    assert parsed.summary["ai_opportunity_traffic"] == {"current": 2000.0, "previous": None}


def test_a_live_answer_for_the_running_month_has_no_brand_presence_for_the_month_asked() -> None:
    parsed = parse_overview(_live(date(2026, 9, 1)), date(2026, 8, 1))
    assert parsed.realigned is True
    assert parsed.summary["link_presence"] == {"current": 110.0, "previous": 100.0}
    assert parsed.summary["brand_presence"] == {"current": None, "previous": None}


def test_position_zero_is_no_position() -> None:
    """ChatGPT for bol.com, NL database: counts, no series, ``average_position: 0``."""
    body = _live(date(2026, 8, 1), summary={"average_position": 0})
    body["time_series"] = {stream: [] for stream in body["time_series"]}
    parsed = parse_overview(body, date(2026, 8, 1))
    assert parsed.no_data is False
    assert parsed.summary["average_position"] == {"current": None, "previous": None}
    assert parsed.summary["link_presence"]["current"] == 120.0


def test_no_index_is_a_state_not_four_empty_figures() -> None:
    """An engine SE Ranking does not track for this country, or a site it has never seen,
    answers 200 with ``no_index`` and nulls — which must not read as "invisible in AI"."""
    body = {
        "no_index": True,
        "brand": "acme",
        "time_series": {"link_presence": [], "average_position": []},
        "summary": {
            key: {"current": None, "previous": None} for key in _body(date(2026, 8, 1))["summary"]
        },
    }
    parsed = parse_overview(body, date(2026, 8, 1))
    assert parsed.no_data is True
    assert parsed.data_month == date(2026, 8, 1)


# --- the settings ----------------------------------------------------------------------------- #
def test_it_is_off_until_somebody_switches_it_on() -> None:
    assert resolve(None, None) == AiSearchSettings()
    assert AiSearchSettings().enabled is False
    assert AiSearchSettings().engines == ("all",)
    assert AiSearchSettings().monthly_units == 800


def test_a_client_is_a_diff_over_the_house_in_both_directions() -> None:
    house = {"enabled": True, "engines": ["all", "chatgpt"], "source": "nl"}
    assert resolve(house, None).enabled is True
    off = resolve(house, {"enabled": False})
    assert off.enabled is False and off.engines == ("all", "chatgpt")
    one = resolve({"enabled": False}, {"enabled": True, "brand": "Acme", "target": "acme.nl"})
    assert (one.enabled, one.brand, one.target) == (True, "Acme", "acme.nl")
    assert resolve(house, {"engines": ["gemini"]}).monthly_units == 800
    assert resolve(house, None).monthly_units == 1600


def test_the_house_has_no_target_and_no_brand() -> None:
    """One domain's numbers under every client's name is not a default anybody means."""
    house = parse({"enabled": True, "target": "acme.nl", "brand": "Acme"})
    assert (house.target, house.brand) == ("", "")


def test_no_ticked_engine_inherits_rather_than_asking_nothing() -> None:
    assert parse({"engines": []}).engines == ("all",)
    assert parse({"engines": ["bing"]}).engines == ("all",)
    # …and the canonical order is ours, whatever order a form posted them in.
    assert parse({"engines": ["gemini", "all"]}).engines == ("all", "gemini")


def test_a_blank_form_stores_nothing() -> None:
    blank = dict.fromkeys(("enabled", "engines", "source", "scope"), None) | {
        "target": "",
        "brand": "",
    }
    assert diff(blank, client=True) is None
    assert diff({"enabled": False, "target": "https://www.Acme.nl/"}, client=True) == {
        "enabled": False,
        "target": "www.acme.nl",
    }


def test_a_pasted_url_becomes_the_host_unless_it_has_a_path() -> None:
    assert clean_target("https://www.acme.nl/") == "www.acme.nl"
    assert clean_target("Acme.nl") == "acme.nl"
    assert clean_target("https://acme.nl/diensten/") == "https://acme.nl/diensten"
    assert clean_target("") == ""


def test_a_discovered_brand_is_checked_against_who_the_client_is() -> None:
    assert brand_fits("Acme", company="Acme B.V.", target="acme.nl")
    assert brand_fits("Nova Fietsen", company="Nova", target="novafietsen.nl")
    assert not brand_fits("Janssen", company="Fietsenwinkel Goes", target="fietsgoes.nl")


# --- the adapter ------------------------------------------------------------------------------ #
async def test_every_engine_is_its_own_endpoint_and_one_engine_names_itself() -> None:
    fake = _FakeSeRanking(date(2026, 8, 1))
    await ADAPTER.ai_search_overview(fake, target="acme.nl", source="nl")  # type: ignore[arg-type]
    await ADAPTER.ai_search_overview(
        fake, target="acme.nl", source="nl", engine="chatgpt", brand="Acme"  # type: ignore[arg-type]
    )
    (all_url, all_params), (one_url, one_params) = fake.calls
    assert all_url.endswith("/ai-search/overview/aggregated/time-series")
    assert "engine" not in all_params and "brand" not in all_params
    assert one_url.endswith("/ai-search/overview/by-engine/time-series")
    assert (one_params["engine"], one_params["brand"]) == ("chatgpt", "Acme")


async def test_a_success_code_with_no_summary_is_a_refusal() -> None:
    class _Empty(_FakeSeRanking):
        async def get(self, url: str, params: dict | None = None) -> _Response:  # noqa: ARG002
            return _Response({"message": "Something went wrong"})

    with pytest.raises(DataApiRefused) as caught:
        await ADAPTER.ai_search_overview(
            _Empty(date(2026, 8, 1)), target="acme.nl", source="nl"  # type: ignore[arg-type]
        )
    assert caught.value.kind == "failed"


def test_a_refusal_is_named_for_who_can_fix_it() -> None:
    assert classify_refusal(403, {"message": "Forbidden"}).kind == "denied"
    assert classify_refusal(401, "nope").kind == "denied"
    spent = classify_refusal(400, {"error": {"message": "Insufficient funds"}})
    assert spent.kind == "insufficient"
    assert classify_refusal(500, {}).kind == "failed"


# --- the read, end to end --------------------------------------------------------------------- #
async def _setup(c, headers, *, enabled: bool = True, key: bool = True) -> str:  # noqa: ANN001
    company = (await c.post("/api/v1/companies", json={"name": "Acme BV"}, headers=headers)).json()
    body: dict[str, Any] = {"ai_search": {"enabled": enabled}}
    if key:
        body["seranking_api_key"] = "shared-key"
    saved_org = await c.put("/api/v1/marketing/settings", json=body, headers=headers)
    assert saved_org.status_code == 200, saved_org.text
    saved = await c.put(
        f"/api/v1/marketing/companies/{company['id']}/ai-search/settings",
        json={"target": "https://www.acme.nl/"},
        headers=headers,
    )
    assert saved.status_code == 200, saved.text
    return company["id"]


async def test_nothing_is_asked_until_the_overview_is_switched_on(client_for, seranking) -> None:
    t = await make_tenant("ais-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers, enabled=False)
        body = (
            await c.get(f"/api/v1/marketing/companies/{company_id}/ai-search", headers=headers)
        ).json()
    assert body["state"] == "off"
    assert body["engines"] == []
    assert seranking.calls == []


async def test_last_month_is_fetched_once_and_then_read(client_for, seranking) -> None:
    t = await make_tenant("ais-once")
    headers = await auth_cookie(t.user)
    month = expected_month(org_today())
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        first = (await c.get(url, headers=headers)).json()
        second = (await c.get(url, headers=headers)).json()

    assert first["state"] == "ready"
    assert first["period_month"] == month.isoformat()
    assert first["settings"]["target"] == "www.acme.nl"
    assert first["target_origin"] == "setting"
    # The brand was SE Ranking's own attribution, read once, sent explicitly and shown.
    assert (first["brand"], first["brand_origin"]) == ("Acme", "discovered")
    assert first["brand_fits"] is True
    (block,) = first["engines"]
    assert (block["engine"], block["status"]) == ("all", "ok")
    assert block["data_month"] == month.isoformat()
    assert block["compare_month"] == previous_month(month).isoformat()
    by_key = {metric["key"]: metric for metric in block["metrics"]}
    assert [m["key"] for m in block["metrics"]] == [
        "brand_presence", "link_presence", "average_position", "ai_opportunity_traffic",
    ]
    assert by_key["brand_presence"]["current"] == 50
    assert by_key["brand_presence"]["change_percent"] == 11.1
    assert (by_key["average_position"]["direction"], by_key["average_position"]["verdict"]) == (
        "down", "good",
    )
    assert len(block["series"]["ai_traffic"]) == 3
    # One brand lookup, one paid read — and the second page view cost nothing.
    assert len(seranking.paid()) == 1
    assert seranking.paid()[0][1]["brand"] == "Acme"
    assert second["engines"][0]["metrics"] == block["metrics"]
    assert first["units_left"] == 50_000 - 800


async def test_each_picked_engine_is_its_own_block(client_for, seranking) -> None:
    t = await make_tenant("ais-engines")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        await c.put(
            f"/api/v1/marketing/companies/{company_id}/ai-search/settings",
            json={"target": "acme.nl", "brand": "Acme", "engines": ["chatgpt", "all"]},
            headers=headers,
        )
        body = (
            await c.get(f"/api/v1/marketing/companies/{company_id}/ai-search", headers=headers)
        ).json()
    assert [block["engine"] for block in body["engines"]] == ["all", "chatgpt"]
    assert body["brand_origin"] == "setting"
    assert body["settings"]["monthly_units"] == 1600
    # A typed brand needs no lookup.
    assert not any(url.endswith("discover-brand") for url, _ in seranking.calls)
    assert {call[0].rsplit("/", 2)[-2] for call in seranking.paid()} == {"aggregated", "by-engine"}


async def test_a_key_without_data_api_access_is_named_and_not_asked_again(
    client_for, seranking
) -> None:
    t = await make_tenant("ais-denied")
    headers = await auth_cookie(t.user)
    seranking.refuse = (403, {"message": "Access denied"})
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        body = (await c.get(url, headers=headers)).json()
        assert body["engines"][0]["status"] == "denied"
        assert body["engines"][0]["metrics"] == []
        # The probe said no, so no paid call was even attempted…
        assert seranking.paid() == []
        calls = len(seranking.calls)
        await c.get(url, headers=headers)
        # …and the stored refusal answers the next page view.
        assert len(seranking.calls) == calls

        # A manager who fixed the key asks again at once.
        seranking.refuse = None
        again = (await c.post(f"{url}/refresh", headers=headers)).json()
    assert again["engines"][0]["status"] == "ok"
    assert len(seranking.paid()) == 1


async def test_a_refused_refresh_keeps_the_month_that_was_already_stored(
    client_for, seranking
) -> None:
    """*Vernieuwen* on a good month, refused: the answer stays, and the response says why it is
    not newer. Overwriting a stored month with a refusal would lose figures a report may
    already have printed — for nothing, since the refusal carries none of its own."""
    t = await make_tenant("ais-keep")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        await c.get(url, headers=headers)
        seranking.units_left = 100
        refreshed = (await c.post(f"{url}/refresh", headers=headers)).json()
        assert refreshed["notice"] == "insufficient"
        assert refreshed["engines"][0]["status"] == "ok"
        assert len(refreshed["engines"][0]["metrics"]) == 4
        # …and the next ordinary read is quiet: good figures, no notice, no new call.
        calls = len(seranking.calls)
        again = (await c.get(url, headers=headers)).json()
    assert again["notice"] is None
    assert again["engines"][0]["status"] == "ok"
    assert len(seranking.calls) == calls
    assert len(seranking.paid()) == 1


async def test_a_re_read_that_comes_back_older_never_replaces_the_right_month(
    client_for, seranking
) -> None:
    t = await make_tenant("ais-older")
    headers = await auth_cookie(t.user)
    month = expected_month(org_today())
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        await c.get(url, headers=headers)
        seranking.latest = previous_month(month)
        refreshed = (await c.post(f"{url}/refresh", headers=headers)).json()
    assert refreshed["engines"][0]["data_month"] == month.isoformat()
    assert refreshed["engines"][0]["status"] == "ok"


async def test_a_manager_sees_the_derived_site_before_switching_it_on(
    client_for, seranking
) -> None:
    """Off, and nothing asked of SE Ranking — but the editor's domain box needs a placeholder,
    so the site that *would* be read is derived for whoever may switch it on."""
    t = await make_tenant("ais-derive")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Acme BV"}, headers=headers)
        ).json()
        linked = await c.post(
            "/api/v1/marketing/links",
            json={
                "company_id": company["id"],
                "source": "seranking",
                "external_id": "4410021",
                "display_name": "Acme",
                "config": {"url": "https://www.acme.nl/"},
            },
            headers=headers,
        )
        assert linked.status_code in (200, 201), linked.text
        body = (
            await c.get(f"/api/v1/marketing/companies/{company['id']}/ai-search", headers=headers)
        ).json()
    assert body["state"] == "off"
    assert (body["settings"]["target"], body["target_origin"]) == ("www.acme.nl", "seranking")
    assert seranking.paid() == []


async def test_a_plan_out_of_units_is_not_charged_to_find_that_out(client_for, seranking) -> None:
    t = await make_tenant("ais-units")
    headers = await auth_cookie(t.user)
    seranking.units_left = 300
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        body = (
            await c.get(f"/api/v1/marketing/companies/{company_id}/ai-search", headers=headers)
        ).json()
    assert body["engines"][0]["status"] == "insufficient"
    assert seranking.paid() == []


async def test_an_older_month_keeps_showing_while_this_one_is_refused(
    client_for, seranking
) -> None:
    """A refusal this month must not blank the dashboard: last month's stored figures are
    shown, *as last month's*, with the refusal still reported to whoever can fix it."""
    t = await make_tenant("ais-fallback")
    headers = await auth_cookie(t.user)
    month = expected_month(org_today())
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        await c.get(url, headers=headers)
        # Age the stored month by one: it is now "the month before".
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(
                update(MarketingAiSearchSnapshot).values(
                    period_month=previous_month(month), data_month=previous_month(month)
                )
            )
            await session.commit()
        seranking.refuse = (403, {"message": "Access denied"})
        body = (await c.get(url, headers=headers)).json()
    (block,) = body["engines"]
    assert block["status"] == "denied"
    assert block["data_month"] == previous_month(month).isoformat()
    assert block["period_month"] == month.isoformat()
    assert len(block["metrics"]) == 4


async def test_a_separate_data_api_key_is_the_one_the_data_api_is_asked_with(
    client_for, seranking
) -> None:
    t = await make_tenant("ais-datakey")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        settings = (
            await c.put(
                "/api/v1/marketing/settings",
                json={"seranking_data_api_key": "data-key"},
                headers=headers,
            )
        ).json()
        assert settings["seranking_data_api_key_configured"] is True
        await c.get(f"/api/v1/marketing/companies/{company_id}/ai-search", headers=headers)
        assert set(seranking.keys) == {"data-key"}

        check = (
            await c.get("/api/v1/marketing/settings/seranking/check", headers=headers)
        ).json()
        assert (check["project_api"], check["data_api"], check["data_api_key"]) == (
            "ok", "ok", "own",
        )
        assert check["units_left"] == 50_000
        assert (check["enabled_clients"], check["monthly_units"]) == (0, 0)

        # Removing it is its own statement — an empty box means "keep it".
        cleared = (
            await c.put(
                "/api/v1/marketing/settings",
                json={"clear_seranking_data_api_key": True},
                headers=headers,
            )
        ).json()
    assert cleared["seranking_data_api_key_configured"] is False
    assert cleared["seranking_api_key_configured"] is True


async def test_the_check_says_which_api_refused(client_for, seranking) -> None:
    t = await make_tenant("ais-check")
    headers = await auth_cookie(t.user)
    seranking.refuse = (403, {"message": "Access denied"})
    async with client_for(t.host) as c:
        await _setup(c, headers)
        check = (
            await c.get("/api/v1/marketing/settings/seranking/check", headers=headers)
        ).json()
    # The project API answered; only the Data API refused — two sentences, not one verdict.
    assert (check["project_api"], check["data_api"]) == ("ok", "denied")


async def test_another_tenant_sees_neither_the_client_nor_its_months(client_for, seranking) -> None:
    a = await make_tenant("ais-iso-a")
    b = await make_tenant("ais-iso-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)
    async with client_for(a.host) as c:
        company_id = await _setup(c, headers_a)
        await c.get(f"/api/v1/marketing/companies/{company_id}/ai-search", headers=headers_a)
    async with client_for(b.host) as c:
        refused = await c.get(
            f"/api/v1/marketing/companies/{company_id}/ai-search", headers=headers_b
        )
        assert refused.status_code == 404
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        rows = (await session.execute(select(MarketingAiSearchSnapshot))).scalars().all()
    assert rows == []


async def test_a_reader_gets_the_figures_and_none_of_the_controls(client_for, seranking) -> None:
    from tests.test_marketing_api import _add_member

    t = await make_tenant("ais-member")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, owner)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        await c.get(url, headers=owner)
        member = await _add_member(t.org.id, "reader@ais-member.test")
        headers = await auth_cookie(member)
        body = (await c.get(url, headers=headers)).json()
        assert body["engines"][0]["status"] == "ok"
        assert body["can_manage"] is False
        assert body["own"] is None and body["house"] is None and body["units_left"] is None
        assert (await c.post(f"{url}/refresh", headers=headers)).status_code == 403
        assert (
            await c.put(f"{url}/settings", json={"enabled": False}, headers=headers)
        ).status_code == 403


async def _portal_headers(c, headers, company_id: str, email: str) -> dict[str, str]:  # noqa: ANN001
    from app.core.auth.models import User as AuthUser

    contact = (
        await c.post(
            "/api/v1/contacts",
            json={
                "first_name": "Piet",
                "last_name": "Klant",
                "email": email,
                "company_ids": [company_id],
            },
            headers=headers,
        )
    ).json()
    invited = await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
    assert invited.status_code == 200, invited.text
    async with async_session_maker() as session:
        user = await session.scalar(select(AuthUser).where(AuthUser.email == email))
    return await auth_cookie(user)


async def test_a_client_gets_the_figures_and_nothing_about_the_agencys_desk(
    client_for, seranking
) -> None:
    t = await make_tenant("ais-portal")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, owner)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        await c.get(url, headers=owner)
        portal = await _portal_headers(c, owner, company_id, "piet@ais-portal.test")
        body = (await c.get(url, headers=portal)).json()
        assert body["state"] == "ready"
        assert body["engines"][0]["metrics"][0]["current"] == 50
        assert body["can_manage"] is False
        assert body["own"] is None and body["house"] is None
        assert body["units_left"] is None and body["notice"] is None
        assert body["discovered_brands"] == []


async def test_to_a_client_a_refusal_or_a_missing_key_is_no_section_at_all(
    client_for, seranking
) -> None:
    """"De API-sleutel heeft geen toegang tot de Data API" and "er is geen sleutel opgeslagen"
    are sentences about the agency's desk, naming a supplier and a settings screen a client
    cannot open (#446). ``_block`` leaves a refused block out for a portal reader; with every
    block left out — or no key, or no domain — there is nothing to draw, which is ``off``."""
    t = await make_tenant("ais-portal-off")
    owner = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        seranking.refuse = (403, {"message": "Forbidden"})
        company_id = await _setup(c, owner)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        staff = (await c.get(url, headers=owner)).json()
        assert (staff["state"], staff["engines"][0]["status"]) == ("ready", "denied")

        portal = await _portal_headers(c, owner, company_id, "piet@ais-portal-off.test")
        refused = (await c.get(url, headers=portal)).json()
        assert (refused["state"], refused["engines"], refused["brand"]) == ("off", [], "")

        # No domain to derive: the manager is told what to fill in, the client is shown nothing.
        other = (
            await c.post("/api/v1/companies", json={"name": "Geen Domein BV"}, headers=owner)
        ).json()
        portal_other = await _portal_headers(c, owner, other["id"], "kees@ais-portal-off.test")
        other_url = f"/api/v1/marketing/companies/{other['id']}/ai-search"
        assert (await c.get(other_url, headers=owner)).json()["state"] == "no_target"
        assert (await c.get(other_url, headers=portal_other)).json()["state"] == "off"


def test_the_month_a_read_is_about_is_the_last_complete_one() -> None:
    assert expected_month(date(2026, 9, 18)) == date(2026, 8, 1)
    assert expected_month(date(2026, 1, 1)) == date(2025, 12, 1)
    assert expected_month(date(2026, 3, 31) + timedelta(days=1)) == date(2026, 3, 1)


# --- the report chapter ------------------------------------------------------------------------ #
async def _chapter(org_id, company_id: str, month: date):  # noqa: ANN001, ANN202
    """Run the section provider the way the report worker does: a system context, no request."""
    import uuid as _uuid

    from app.core.jobs import system_context
    from app.core.models import Org
    from app.modules.marketing.report_sections import _ai_search_overview, _month_end
    from app.registry import ReportWindow

    async with async_session_maker() as session:
        await set_current_org(session, org_id)
        org = await session.get(Org, org_id)
        window = ReportWindow(
            company_id=_uuid.UUID(company_id),
            start=month,
            end=_month_end(month),
            compare_start=None,
            compare_end=None,
            locale="nl",
        )
        payload = await _ai_search_overview(system_context(org, session), window)
        await session.commit()
        return payload


async def test_the_report_reads_the_month_it_is_about_against_the_month_before(
    client_for, seranking
) -> None:
    """The run is usually what fetches the month — and it states its own comparison span,
    because the cover's "vergeleken met …" is about the traffic chapters, a year back."""
    t = await make_tenant("ais-report")
    headers = await auth_cookie(t.user)
    month = expected_month(org_today())
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
    payload = await _chapter(t.org.id, company_id, month)

    assert payload is not None and payload["kind"] == "ai_search_overview"
    assert payload["totals"] == {
        "ai_brand_presence": 50.0,
        "ai_link_presence": 120.0,
        "ai_average_position": 8.5,
        "ai_opportunity_traffic": 2000.0,
    }
    assert payload["compare"]["ai_average_position"] == 9.2
    before = previous_month(month)
    assert payload["compare_period"]["start"] == before.isoformat()
    assert payload["chart"]["labels"][-1] == month.strftime("%Y-%m")
    assert payload["rows"] == []  # one engine choice: tiles and a trend, no table of one row
    assert len(seranking.paid()) == 1

    # A second run (a re-generated report) is a database read.
    await _chapter(t.org.id, company_id, month)
    assert len(seranking.paid()) == 1


async def test_a_month_the_vendor_has_not_published_is_withheld_from_the_report(
    client_for, seranking
) -> None:
    """July's AI figures under an August cover would be the one number on the document from
    another month. The chapter is left out and the run says why — to the agency."""
    t = await make_tenant("ais-report-lag")
    headers = await auth_cookie(t.user)
    month = expected_month(org_today())
    seranking.latest = previous_month(month)
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
    payload = await _chapter(t.org.id, company_id, month)
    assert payload == {
        "withheld": True,
        "notes": [{"code": "reporting.warning.seranking_ai_overview_lagging", "detail": "all"}],
    }


async def test_a_client_without_the_overview_has_no_chapter_and_no_warning(
    client_for, seranking
) -> None:
    t = await make_tenant("ais-report-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers, enabled=False)
    assert await _chapter(t.org.id, company_id, expected_month(org_today())) is None
    assert seranking.calls == []


def test_the_chapter_prints_its_four_figures_the_month_before_and_a_monthly_trend() -> None:
    from app.modules.reporting.render import context as ctx
    from app.modules.reporting.render.engine import ENGINE

    snapshot = {
        "company": {"name": "Acme B.V."},
        "period": {"label": "augustus 2026"},
        "compare": {"label": "augustus 2025"},
        "order": ["marketing.ai_search_overview"],
        "sections": {
            "marketing.ai_search_overview": {
                "kind": "ai_search_overview",
                "columns": [],
                "rows": [],
                "totals": {
                    "ai_brand_presence": 50,
                    "ai_link_presence": 120,
                    "ai_average_position": 8.5,
                    "ai_opportunity_traffic": 2000,
                },
                "compare": {
                    "ai_brand_presence": 45,
                    "ai_link_presence": 110,
                    "ai_average_position": 9.2,
                    "ai_opportunity_traffic": 1800,
                },
                "compare_period": {"start": "2026-07-01", "end": "2026-07-31"},
                "chart": {
                    "type": "grouped",
                    "labels": ["2026-06", "2026-07", "2026-08"],
                    "series": [{"key": "current", "values": [100, 110, 120]}],
                    "metric": "ai_link_presence",
                },
            }
        },
    }

    class _Report:
        title = "Maandrapport"
        company_name = "Acme B.V."

    context = ctx.build_context(
        report=_Report(), snapshot=snapshot, narrative={}, section_titles={},
        brand_name="Bureau", logo_uri=None, cover_uri=None, client_logo_uri=None,
        accent=None, intro_text=None, footer_text=None, locale="nl", internal=False,
    )
    (section,) = context["sections"]
    tiles = {tile["key"]: tile for tile in section["parts"][0]["totals"]}
    # Reading order is stated, not inherited from a JSONB column that has none.
    assert list(tiles) == [
        "ai_brand_presence", "ai_link_presence", "ai_average_position", "ai_opportunity_traffic",
    ]
    assert tiles["ai_brand_presence"]["label"] == "Merkvermeldingen in AI"
    assert tiles["ai_average_position"]["value"] == "8,5"
    # A position that fell is an improvement: the verdict is good.
    assert tiles["ai_average_position"]["delta_class"] == "up"
    # …and the *drawn* badge agrees: a down arrow, in the good colour. It used to take its
    # verdict from the key "delta", so every lower-is-better tile printed its improvement red.
    badge = str(tiles["ai_average_position"]["badge"])
    assert 'class="badge up"' in badge and "-7,6%" in badge
    assert 'class="badge up"' in str(tiles["ai_brand_presence"]["badge"])
    assert section["parts"][0]["compare_label"] == "juli 2026"
    # This chapter is all the client has, so its tiles lead the cover — and the cover's caption
    # names the span *those* tiles were measured against, not the report's own.
    assert context["cover_compare_label"] == "juli 2026"
    html = ENGINE.render_html(context, {})
    # The chapter has no table to name what its bars are, so the chart says so itself.
    assert section["parts"][0]["chart_caption"] == "Links in AI-antwoorden"
    assert 'class="chart-caption">Links in AI-antwoorden<' in html
    cover_foot = html.split('class="cover-foot"', 1)[1].split("</div>", 1)[0]
    assert "juli 2026" in cover_foot and "augustus 2025" not in cover_foot
    assert ">aug<" in html.replace(" ", "") or "aug" in html
    assert "marketing.metric" not in html  # no message key ever prints on a client's page



def test_the_covers_caption_follows_the_tiles_above_it() -> None:
    """A percentage is a claim about two spans (#312). A year-over-year section leads the cover
    whenever there is one, under the report's own caption; a section with a span of its own
    leads only when it is all there is — and then the caption is that span."""
    from app.modules.reporting.render.context import _headline_span

    traffic = {"totals": [{"key": "sessions"}], "compare_label": None}
    ai = {"totals": [{"key": "ai_brand_presence"}], "compare_label": "juli 2026"}
    assert _headline_span([ai, traffic], "augustus 2025") == "augustus 2025"
    assert _headline_span([ai], "augustus 2025") == "juli 2026"
    assert _headline_span([], "augustus 2025") == "augustus 2025"
    assert _headline_span([traffic], None) is None


async def test_a_domain_se_ranking_has_no_answers_for_is_said_not_drawn(
    client_for, seranking
) -> None:
    t = await make_tenant("ais-noindex")
    owner = await auth_cookie(t.user)
    month = expected_month(org_today())
    seranking.body = {
        "no_index": True,
        "time_series": {},
        "summary": {key: {"current": None} for key in _body(month)["summary"]},
    }
    async with client_for(t.host) as c:
        company_id = await _setup(c, owner)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        (block,) = (await c.get(url, headers=owner)).json()["engines"]
        assert (block["status"], block["no_data"], block["metrics"]) == ("ok", True, [])
        portal = await _portal_headers(c, owner, company_id, "piet@ais-noindex.test")
        assert (await c.get(url, headers=portal)).json()["state"] == "off"
    payload = await _chapter(t.org.id, company_id, month)
    assert payload == {
        "withheld": True,
        "notes": [{"code": "reporting.warning.seranking_ai_overview_no_data", "detail": "all"}],
    }


async def test_brand_presence_is_compared_with_the_month_we_stored_before(
    client_for, seranking
) -> None:
    """The vendor has no ``previous`` and brand presence has no series, so the only month
    before it there can ever be is the one this instance read a month ago."""
    t = await make_tenant("ais-ourprev")
    headers = await auth_cookie(t.user)
    month = expected_month(org_today())
    before = previous_month(month)
    seranking.body = _live(before, summary={"brand_presence": 40})
    seranking.latest = before
    async with client_for(t.host) as c:
        company_id = await _setup(c, headers)
        url = f"/api/v1/marketing/companies/{company_id}/ai-search"
        await c.get(url, headers=headers)
        # That read is last month's, stored under last month.
        async with async_session_maker() as session:
            await set_current_org(session, t.org.id)
            await session.execute(update(MarketingAiSearchSnapshot).values(period_month=before))
            await session.commit()
        seranking.body = _live(month)
        (block,) = (await c.get(url, headers=headers)).json()["engines"]
    by_key = {metric["key"]: metric for metric in block["metrics"]}
    assert block["data_month"] == month.isoformat()
    assert (by_key["brand_presence"]["current"], by_key["brand_presence"]["previous"]) == (50, 40)
    assert by_key["brand_presence"]["change_percent"] == 25.0
    # The series still wins where there is one.
    assert by_key["link_presence"]["previous"] == 110
