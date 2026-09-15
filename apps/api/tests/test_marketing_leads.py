"""The leads dashboard and its measurement profile (docs/MARKETING.md).

Four groups, each a rule the code would otherwise only look like it follows.

1. **A period can be a free span.** ``2026-08-29..2026-09-03`` is a token like ``30d``, clamped
   the same three ways, so a breakpoint can be the floor of a URL and of an MCP call alike.
2. **The GA4 filter tree is built, not typed.** OR across a role's matchers, AND with the
   reader's filters, regex where a client's event names differ by suffix — and the local
   predicate agrees with the server-side filter, because one report carries several roles.
3. **The Jachttrans acceptance period reproduces** (spec §9): the numbers a Looker report was
   checked against on 4 September 2026 come out of the widget arithmetic from GA4-shaped rows —
   including the funnel row with starts and no submissions, and no division by zero.
4. **A widget is withheld, never zeroed**, when the profile lacks what it needs; a client
   login never receives the agency's diagnostics; and a filter the profile cannot express is
   refused rather than dropped.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from typing import Any
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import select

from app.core.auth.models import User
from app.core.crypto import encrypt
from app.core.googleads import set_transport as set_ads_transport
from app.core.periods import range_token, resolve_period
from app.db import async_session_maker, set_current_org
from app.integrations.google.models import ConnectionStatus, GoogleConnection, GoogleSettings
from app.integrations.google.oauth import SCOPE_ADS, SCOPE_ANALYTICS
from app.modules.marketing.leads import ga4
from app.modules.marketing.leads.filters import all_of, event_filter, matches, not_
from app.modules.marketing.leads.profile import (
    DEFAULT_CHANNEL_GROUPS,
    EventMatcher,
    LeadProfile,
    channel_group_for,
    parse_profile,
    resolve_channel_groups,
)
from app.modules.marketing.leads.service import set_transport as set_ga4_transport
from app.modules.marketing.leads.widgets import WIDGET_KEYS, compute
from app.modules.marketing.models import MarketingLink, MarketingSource
from tests.conftest import auth_cookie, make_tenant
from tests.googleads_fake import FakeGoogleAds, metrics

START, END = date(2026, 8, 29), date(2026, 9, 3)
DIENST, FORMTYPE, TAAL, TITEL, FOUT = (
    "customEvent:dienst",
    "customEvent:formulier_type",
    "customEvent:formulier_taal",
    "customEvent:pagina_titel",
    "customEvent:foutreden",
)

#: The validation client's profile (spec §3.2) — the one example, never the code's default.
JACHTTRANS: dict[str, Any] = {
    "roles": {
        "request": [{"match": "exact", "value": "aanvraag_verzonden"}],
        "form_started": [{"match": "exact", "value": "formulier_gestart"}],
        "form_submitted": [{"match": "exact", "value": "formulier_verzonden"}],
        "form_error": [{"match": "exact", "value": "formulier_fout"}],
        "phone_click": [{"match": "begins_with", "value": "click_telefoon"}],
        "email_click": [{"match": "exact", "value": "click_emailformulier"}],
    },
    "dimensions": {
        "service": {
            "field": DIENST,
            "label": {"nl": "Dienst", "en": "Service"},
            "values": {"internationaal-transport": {"nl": "Internationaal transport"}},
        },
        "form_type": {"field": FORMTYPE, "label": {"nl": "Formuliertype"}},
        "language": {"field": TAAL, "label": {"nl": "Taal"}},
        "page_title": {"field": TITEL, "label": {"nl": "Pagina"}},
        "error_reason": {"field": FOUT, "label": {"nl": "Foutreden"}},
    },
    "quote_values": ["offerte"],
    "ads": {"enabled": True, "action_services": {"Offerte - Autotransport": "autotransport"}},
    "breakpoints": [
        {"date": "2026-08-29", "description": {"nl": "Meting herzien"}, "severity": "hard"}
    ],
}

#: APEX (spec §2): separate request events, no service dimension, no Ads account.
APEX: dict[str, Any] = {
    "roles": {
        "request": [
            {"match": "exact", "value": "offerte_aanvraag"},
            {"match": "exact", "value": "contact_aanvraag"},
        ],
        "form_started": [{"match": "exact", "value": "formulier_gestart"}],
        "form_error": [{"match": "exact", "value": "formulier_fout"}],
        "phone_click": [{"match": "exact", "value": "telefoon_klik"}],
        "email_click": [{"match": "exact", "value": "mail_klik"}],
        "application": [{"match": "exact", "value": "sollicitatie"}],
    },
    "dimensions": {
        "form_type": {"field": FORMTYPE, "label": {"nl": "Formuliertype"}},
        "page_path": {"field": "customEvent:pagina_pad", "label": {"nl": "Pagina"}},
    },
    "quote_values": ["offerte"],
    "ads": {"enabled": False},
}


# --- the canned GA4 answers, from the acceptance table -------------------------------------- #
def _report(
    dims: list[str], rows: list[tuple[list[str], list[float]]], metric: str = "eventCount"
) -> dict:
    return {
        "dimensionHeaders": [{"name": d} for d in dims],
        "metricHeaders": [{"name": metric, "type": "TYPE_INTEGER"}],
        "rows": [
            {
                "dimensionValues": [{"value": v} for v in dvals],
                "metricValues": [{"value": str(m)} for m in mvals],
            }
            for dvals, mvals in rows
        ],
        "rowCount": len(rows),
        "metadata": {"currencyCode": "EUR", "timeZone": "Europe/Amsterdam"},
    }


def _canned() -> dict[tuple[str, ...], dict]:
    services_requests = {
        "autotransport": 17,
        "overig": 11,
        "internationaal-transport": 2,
        "motortransport": 2,
        "verhuizen": 2,
        "autostalling": 1,
    }
    started = {
        "autotransport": 26,
        "overig": 15,
        "verhuizen": 6,
        "internationaal-transport": 5,
        "motortransport": 3,
        "autostalling": 2,
        "werken-bij": 1,
    }
    submitted = {
        "autotransport": 17,
        "overig": 12,
        "verhuizen": 1,
        "internationaal-transport": 2,
        "motortransport": 2,
        "autostalling": 1,
    }
    channels = {
        "Organic Search": 19,
        "Cross-network": 8,
        "Direct": 4,
        "Unassigned": 3,
        "AI Assistant": 1,
    }
    daily_requests = {
        "20260829": 5,
        "20260830": 7,
        "20260831": 6,
        "20260901": 4,
        "20260902": 8,
        "20260903": 5,
    }
    return {
        ("eventName", FORMTYPE): _report(
            ["eventName", FORMTYPE],
            [
                (["aanvraag_verzonden", "offerte"], [21]),
                (["aanvraag_verzonden", "contact"], [7]),
                (["aanvraag_verzonden", "vraag"], [7]),
                (["formulier_verzonden", "offerte"], [21]),
                (["formulier_verzonden", "contact"], [7]),
                (["formulier_verzonden", "vraag"], [7]),
                (["formulier_verzonden", "sollicitatie"], [1]),
                (["formulier_gestart", "offerte"], [40]),
                (["formulier_gestart", "contact"], [10]),
                (["formulier_gestart", "vraag"], [8]),
                (["formulier_fout", "offerte"], [21]),
                (["click_telefoon_430", "(not set)"], [60]),
                (["click_telefoon_es_065", "(not set)"], [5]),
                (["click_emailformulier", "(not set)"], [16]),
            ],
        ),
        ("date", "eventName"): _report(
            ["date", "eventName"],
            [([day, "aanvraag_verzonden"], [n]) for day, n in daily_requests.items()]
            + [([day, "formulier_gestart"], [n + 4]) for day, n in daily_requests.items()],
        ),
        ("eventName", DIENST): _report(
            ["eventName", DIENST],
            [(["aanvraag_verzonden", s], [n]) for s, n in services_requests.items()]
            + [(["formulier_gestart", s], [n]) for s, n in started.items()]
            + [(["formulier_verzonden", s], [n]) for s, n in submitted.items()],
        ),
        ("sessionDefaultChannelGroup", DIENST): _report(
            ["sessionDefaultChannelGroup", DIENST],
            [
                (["Organic Search", "autotransport"], [12]),
                (["Organic Search", "overig"], [7]),
                (["Cross-network", "verhuizen"], [2]),
                (["Cross-network", "autotransport"], [3]),
                (["Cross-network", "overig"], [3]),
                (["Direct", "autotransport"], [2]),
                (["Direct", "internationaal-transport"], [2]),
                (["Unassigned", "motortransport"], [2]),
                (["Unassigned", "overig"], [1]),
                (["AI Assistant", "autostalling"], [1]),
            ],
        ),
        (TITEL,): _report(
            [TITEL],
            [
                (["Offerte aanvragen Autotransport - Jachttrans"], [16]),
                (["Contact - Jachttrans"], [7]),
                (["Overig"], [12]),
            ],
        ),
        (TAAL,): _report([TAAL], [(["nl"], [31]), (["en"], [2]), (["de"], [1]), (["es"], [1])]),
        (FOUT, TITEL): _report(
            [FOUT, TITEL],
            [
                (["validatiefout", "Offerte aanvragen Autotransport - Jachttrans"], [15]),
                (["validatiefout", "Contact - Jachttrans"], [6]),
            ],
        ),
        ("sessionSourceMedium",): _report(
            ["sessionSourceMedium"], [(["google / organic"], [19]), (["(direct) / (none)"], [4])]
        ),
        ("date",): _report(["date"], [([day], [40]) for day in daily_requests], metric="sessions"),
        # what `_canned()[("sessionDefaultChannelGroup", DIENST)]` sums to per channel:
        ("_channels",): channels,  # type: ignore[dict-item]
    }


class FakeGoogle:
    """GA4 Data + Admin, answering the batch by each request's dimension tuple."""

    def __init__(self) -> None:
        self.canned = _canned()
        self.batches: list[list[dict]] = []
        self.parameters = [
            "dienst",
            "formulier_type",
            "formulier_taal",
            "pagina_titel",
            "foutreden",
        ]
        self.key_events = ["aanvraag_verzonden"]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.startswith("https://oauth2.googleapis.com/token"):
            return httpx.Response(
                200, json={"access_token": "ya29.fake", "expires_in": 3600, "token_type": "Bearer"}
            )
        path = urlparse(url).path
        if "analyticsadmin" in url:
            if path.endswith("/customDimensions"):
                return httpx.Response(
                    200,
                    json={
                        "customDimensions": [
                            {"parameterName": p, "displayName": p} for p in self.parameters
                        ]
                    },
                )
            if path.endswith("/keyEvents"):
                return httpx.Response(
                    200, json={"keyEvents": [{"eventName": k} for k in self.key_events]}
                )
            return httpx.Response(404, json={"error": {"code": 404}})
        body = json.loads(request.content) if request.content else {}
        if path.endswith(":batchRunReports"):
            requests = body.get("requests") or []
            self.batches.append(requests)
            reports = []
            for req in requests:
                dims = tuple(d["name"] for d in req.get("dimensions", []))
                reports.append(self.canned.get(dims) or _report(list(dims), []))
            return httpx.Response(200, json={"reports": reports})
        if path.endswith(":runReport"):
            dims = tuple(d["name"] for d in body.get("dimensions", []))
            if dims == ("eventName",):
                return httpx.Response(
                    200,
                    json=_report(
                        ["eventName"], [(["aanvraag_verzonden"], [77]), (["page_view"], [4000])]
                    ),
                )
            return httpx.Response(200, json=_report(list(dims), []))
        return httpx.Response(404, json={"error": {"code": 404}})


class _FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:  # noqa: ARG002
        self.store[key] = value


# --- 1. the period token ------------------------------------------------------------------ #
def test_a_free_span_is_a_period_token() -> None:
    today = date(2026, 9, 15)
    assert resolve_period("2026-08-29..2026-09-03", today) == (START, END)
    assert range_token(START, END) == "2026-08-29..2026-09-03"
    # Clamped to yesterday, to the cap, and an inverted span falls back rather than answering.
    assert resolve_period("2026-09-01..2026-12-31", today) == (date(2026, 9, 1), date(2026, 9, 14))
    assert resolve_period("2020-01-01..2026-09-14", today, max_days=10) == (
        date(2026, 9, 5),
        date(2026, 9, 14),
    )
    assert resolve_period("2026-09-10..2026-09-01", today) == resolve_period("30d", today)
    assert resolve_period("2026-13-40..2026-09-01", today) == resolve_period("30d", today)


# --- 2. the filter tree --------------------------------------------------------------------- #
def test_filter_tree_is_built_from_matchers() -> None:
    matchers = [
        EventMatcher(match="exact", value="click_emailformulier"),
        EventMatcher(match="exact", value="click_whatsapp"),
        EventMatcher(match="begins_with", value="click_telefoon"),
        EventMatcher(match="regex", value="tel_.*_klik"),
    ]
    expr = event_filter(matchers)
    assert expr is not None and "orGroup" in expr
    parts = expr["orGroup"]["expressions"]
    assert parts[0]["filter"]["inListFilter"]["values"] == [
        "click_emailformulier",
        "click_whatsapp",
    ]
    assert parts[1]["filter"]["stringFilter"]["matchType"] == "BEGINS_WITH"
    assert parts[2]["filter"]["stringFilter"]["matchType"] == "FULL_REGEXP"
    assert parts[2]["filter"]["stringFilter"]["caseSensitive"] is False
    # AND with a reader's filter, and NOT, nest as the API's tree does.
    combined = all_of([expr, {"filter": {"fieldName": DIENST, "inListFilter": {"values": ["x"]}}}])
    assert combined is not None and "andGroup" in combined
    assert "notExpression" in not_(expr)
    # The local predicate agrees with what the server was asked.
    for name, expected in [
        ("click_emailformulier", True),
        ("CLICK_TELEFOON_430", True),
        ("tel_nl_klik", True),
        ("page_view", False),
    ]:
        assert matches(matchers, name) is expected, name
    # One exact matcher is a plain string filter, no group; none is no filter at all.
    single = event_filter([EventMatcher(value="aanvraag_verzonden")])
    assert single is not None and single["filter"]["stringFilter"]["matchType"] == "EXACT"
    assert event_filter([]) is None
    with pytest.raises(ValueError):
        EventMatcher(match="regex", value="(unclosed")


def test_the_plan_asks_only_what_the_profile_can_answer() -> None:
    plan = {spec.key: spec for spec in ga4.build_plan(LeadProfile.model_validate(JACHTTRANS), {})}
    assert set(plan) == {
        ga4.R_EVENTS,
        ga4.R_DAILY,
        ga4.R_SERVICE,
        ga4.R_CHANNEL,
        ga4.R_PAGE,
        ga4.R_LANGUAGE,
        ga4.R_ERRORS,
        ga4.R_SOURCE_MEDIUM,
        ga4.R_TRAFFIC,
    }
    assert plan[ga4.R_CHANNEL].dimensions == ("sessionDefaultChannelGroup", DIENST)
    # A reader's filter rides every filtered report as an AND, on the profile's own field name.
    filtered = {
        spec.key: spec
        for spec in ga4.build_plan(
            LeadProfile.model_validate(JACHTTRANS), {"service": ["autotransport"]}
        )
    }
    body = filtered[ga4.R_EVENTS].request(START, END)
    assert body["dateRanges"] == [{"startDate": "2026-08-29", "endDate": "2026-09-03"}]
    assert "andGroup" in body["dimensionFilter"]
    assert any(
        e.get("filter", {}).get("fieldName") == DIENST
        for e in body["dimensionFilter"]["andGroup"]["expressions"]
    )
    # Traffic is never narrowed by the reader: it is the denominator of the silent-zero check.
    assert "dimensionFilter" not in filtered[ga4.R_TRAFFIC].request(START, END)
    # APEX: no service dimension → no service/channel-by-service reports, no error dimension
    # → the errors report still runs on the page dimension, and never more than two batches.
    apex = {spec.key: spec for spec in ga4.build_plan(LeadProfile.model_validate(APEX), {})}
    assert ga4.R_SERVICE not in apex and ga4.R_LANGUAGE not in apex
    assert apex[ga4.R_CHANNEL].dimensions == ("sessionDefaultChannelGroup",)
    assert apex[ga4.R_ERRORS].dimensions == ("customEvent:pagina_pad",)
    assert len(ga4.batches(list(apex.values()), START, END)) <= 2


# --- 3. the acceptance period ---------------------------------------------------------------- #
def _computed(profile_dict: dict, canned: dict[tuple[str, ...], dict] | None = None):
    profile = LeadProfile.model_validate(profile_dict)
    canned = canned or _canned()
    reports = {
        spec.key: ga4.parse_report(
            spec.key,
            canned.get(spec.dimensions) or _report(list(spec.dimensions), [], spec.metrics[0]),
        )
        for spec in ga4.build_plan(profile, {})
    }
    return compute(
        profile,
        reports,
        start=START,
        end=END,
        locale="nl",
        channel_groups=resolve_channel_groups(None, profile),
        has_ads=False,
    )


def test_jachttrans_acceptance_numbers_reproduce() -> None:
    out = _computed(JACHTTRANS)
    by_key = {w.key: w for w in out.widgets}
    assert by_key["requests"].value == 35
    assert by_key["quotes"].value == 21
    assert by_key["contacts"].value == 81
    assert by_key["failures"].value == 21
    assert [(r.key, r.values["count"]) for r in by_key["requests_by_service"].rows] == [
        ("autotransport", 17),
        ("overig", 11),
        ("internationaal-transport", 2),
        ("motortransport", 2),
        ("verhuizen", 2),
        ("autostalling", 1),
    ]
    # The tenant's label for a value prints; an unlabelled value prints as itself.
    labels = {r.key: r.label for r in by_key["requests_by_service"].rows}
    assert labels["internationaal-transport"] == "Internationaal transport"
    assert labels["autotransport"] == "autotransport"
    assert {r.key: r.values["count"] for r in by_key["requests_by_channel"].rows} == {
        "Organic Search": 19,
        "Cross-network": 8,
        "Direct": 4,
        "Unassigned": 3,
        "AI Assistant": 1,
    }
    assert by_key["requests_by_channel"].total == 35
    # Cross-network is advertising (Performance Max), AI Assistant stays its own group.
    groups = {r.key: r.values["count"] for r in by_key["requests_by_channel_group"].rows}
    assert groups == {"organic": 19, "ads": 8, "ai": 1, "other": 7}
    assert {r.key: r.group for r in by_key["requests_by_channel"].rows}["Cross-network"] == "ads"
    # The pivot: services down, channels across, autotransport highest under Organic Search.
    pivot = by_key["service_by_channel"]
    assert pivot.rows[0].key == "autotransport"
    assert pivot.rows[0].cells["Organic Search"] == 12
    assert [c.key for c in pivot.columns][:2] == ["Organic Search", "Cross-network"]
    # The funnel, left-outer: werken-bij has a start and no submission and is still a row.
    funnel = {r.key: r.values for r in by_key["funnel"].rows}
    assert funnel["autotransport"] == {
        "started": 26,
        "submitted": 17,
        "dropout": pytest.approx(1 - 17 / 26),
    }
    assert funnel["verhuizen"]["dropout"] == pytest.approx(1 - 1 / 6)
    assert funnel["werken-bij"] == {"started": 1, "submitted": 0, "dropout": 1.0}
    dropout_col = next(c for c in by_key["funnel"].columns if c.key == "dropout")
    assert dropout_col.alarm_above == 0.6 and dropout_col.unit == "ratio"
    assert by_key["requests_by_day"].series is not None
    assert sum(by_key["requests_by_day"].series.values["requests"]) == 35
    assert len(by_key["requests_by_day"].series.dates) == 6
    assert "started" in by_key["requests_by_day"].series.values
    assert [(r.key, r.values["count"]) for r in by_key["requests_by_language"].rows][0] == (
        "nl",
        31,
    )
    assert by_key["requests_by_page"].rows[0].values["count"] == 16
    assert (
        by_key["failures_by_reason"].rows == [by_key["failures_by_reason"].rows[0]]
        and by_key["failures_by_reason"].rows[0].key == "validatiefout"
    )
    assert by_key["failures_by_reason"].total == 21
    assert by_key["requests_by_form_type"].total == 35
    # What this profile cannot answer is named, never drawn as zero.
    unavailable = {u.key: u.reason for u in out.unavailable}
    assert unavailable["applications"] == "missing_role:application"
    assert "ads_cost" in unavailable and unavailable["ads_cost"] == "no_ads"
    # Coverage: every request carried a service, so nothing is (not set).
    coverage = {c.dimension: c for c in out.coverage}
    assert coverage["service"].share == 0 and coverage["service"].total == 35
    # The filter controls offer the values seen, without (not set).
    assert set(out.seen_values["service"]) == {
        "autotransport",
        "overig",
        "internationaal-transport",
        "motortransport",
        "verhuizen",
        "autostalling",
    }
    assert not [w for w in out.warnings if w.code == "silent_zero"]


def test_a_missing_role_withholds_the_widget_and_zero_starts_never_divide() -> None:
    profile = json.loads(json.dumps(JACHTTRANS))
    del profile["roles"]["form_started"]
    out = _computed(profile)
    assert "funnel" not in {w.key for w in out.widgets}
    assert {u.key: u.reason for u in out.unavailable}["funnel"] == "missing_role:form_started"
    # No submitted role: the funnel reads the request itself, and says so.
    profile = json.loads(json.dumps(JACHTTRANS))
    del profile["roles"]["form_submitted"]
    funnel = next(w for w in _computed(profile).widgets if w.key == "funnel")
    assert funnel.note_key == "marketing.leads.note.funnel_requests"
    assert {r.key: r.values["submitted"] for r in funnel.rows}["verhuizen"] == 2
    # A service submitted without a recorded start is a row with no dropout, never a negative.
    canned = _canned()
    canned[("eventName", DIENST)] = _report(
        ["eventName", DIENST],
        [(["formulier_gestart", "a"], [0]), (["formulier_verzonden", "b"], [3])],
    )
    funnel = next(w for w in _computed(JACHTTRANS, canned).widgets if w.key == "funnel")
    assert {r.key: r.values["dropout"] for r in funnel.rows} == {"a": None, "b": None}


def test_apex_is_configuration_only() -> None:
    """A second client with different event names and fewer dimensions: no code, no zeros."""
    canned = {
        ("eventName", FORMTYPE): _report(
            ["eventName", FORMTYPE],
            [
                (["offerte_aanvraag", "offerte"], [4]),
                (["contact_aanvraag", "contact"], [3]),
                (["sollicitatie", "sollicitatie"], [2]),
                (["telefoon_klik", "(not set)"], [9]),
                (["formulier_gestart", "offerte"], [20]),
                (["formulier_fout", "offerte"], [1]),
            ],
        ),
        ("sessionDefaultChannelGroup",): _report(
            ["sessionDefaultChannelGroup"], [(["Organic Search"], [5]), (["Direct"], [2])]
        ),
    }
    out = _computed(APEX, canned)
    by_key = {w.key: w for w in out.widgets}
    assert by_key["requests"].value == 7
    assert by_key["applications"].value == 2
    assert by_key["contacts"].value == 9
    assert by_key["quotes"].value == 4
    keys = set(by_key)
    assert {
        "requests_by_service",
        "funnel",
        "service_by_channel",
        "requests_by_language",
    }.isdisjoint(keys)
    reasons = {u.key: u.reason for u in out.unavailable}
    assert reasons["requests_by_service"] == "missing_dimension:service"
    assert reasons["ads_cost"] == "no_ads"


def test_quality_flags_and_the_silent_zero() -> None:
    canned = _canned()
    canned[("date", "eventName")] = _report(
        ["date", "eventName"], [(["20260829", "formulier_gestart"], [3])]
    )
    canned[("date", "eventName")]["metadata"]["samplingMetadatas"] = [{"samplesReadCount": "1"}]
    canned[("eventName", FORMTYPE)]["metadata"]["subjectToThresholding"] = True
    out = _computed(JACHTTRANS, canned)
    codes = {w.code for w in out.warnings}
    assert {"sampled", "thresholded", "silent_zero"} <= codes
    assert "other_row" not in codes


def test_profile_validation_and_channel_groups() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        LeadProfile.model_validate({"roles": {"lead": []}})
    with pytest.raises(ValueError, match="unknown dimension"):
        LeadProfile.model_validate({"dimensions": {"colour": {"field": "x"}}})
    with pytest.raises(ValueError, match="unknown widget"):
        LeadProfile.model_validate({"hidden_widgets": ["ads_roas"]})
    with pytest.raises(ValueError, match="unknown channel group"):
        LeadProfile.model_validate({"channel_groups": {"paid": ["Paid Search"]}})
    # A stored shape a later release refuses reads as absent rather than 500-ing.
    assert parse_profile({"roles": {"nonsense": []}}) is None
    assert parse_profile(None) is None
    # An empty role is the same fact as no role; breakpoints sort; the hard one is the floor.
    profile = LeadProfile.model_validate(
        {
            "roles": {"request": [], "phone_click": [{"value": "tel"}]},
            "breakpoints": [
                {"date": "2026-09-01", "severity": "soft"},
                {"date": "2026-08-01", "severity": "hard"},
            ],
        }
    )
    assert not profile.has_role("request") and profile.has_role("phone_click")
    assert profile.hard_breakpoint() is not None and profile.hard_breakpoint().date == date(
        2026, 8, 1
    )
    # Grouping: the client's, else the agency's, else the default that files PMax under ads.
    assert channel_group_for("Cross-network", DEFAULT_CHANNEL_GROUPS) == "ads"
    assert channel_group_for("Email", DEFAULT_CHANNEL_GROUPS) == "other"
    org = {"organic": ["Organic Search"], "ads": ["Paid Search"]}
    assert resolve_channel_groups(org, profile) == org
    assert resolve_channel_groups({"bogus": ["x"]}, profile) == DEFAULT_CHANNEL_GROUPS
    assert set(WIDGET_KEYS) >= {"requests", "funnel", "ads_conversion_actions"}


# --- 4. end to end ---------------------------------------------------------------------------- #
async def _connected(slug: str):
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
                scopes=[SCOPE_ANALYTICS, SCOPE_ADS],
                refresh_token_encrypted=encrypt("1//fake-refresh-token"),
                status=ConnectionStatus.ACTIVE.value,
            )
        )
        await session.commit()
    return t


@pytest.fixture
def fakes(monkeypatch):
    redis = _FakeRedis()
    monkeypatch.setattr("app.modules.marketing.leads.service.get_redis", lambda: redis)
    google = FakeGoogle()
    ads = FakeGoogleAds()
    set_ga4_transport(google.transport())
    set_ads_transport(ads.transport())
    try:
        yield google, ads, redis
    finally:
        set_ga4_transport(None)
        set_ads_transport(None)


async def _link(
    c, headers, company_id: str, source: str, external_id: str, config: dict | None = None
) -> str:
    res = await c.post(
        "/api/v1/marketing/links",
        json={
            "company_id": company_id,
            "source": source,
            "external_id": external_id,
            "display_name": external_id,
            "config": config or {},
        },
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def test_dashboard_end_to_end_staff_and_client(client_for, fakes) -> None:
    google, ads, _redis = fakes
    t = await _connected("leads-e2e")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Jachttrans"}, headers=headers)
        ).json()
        url = f"/api/v1/marketing/companies/{company['id']}/leads"

        # No profile: a state the screen teaches from, not an error.
        bare = (await c.get(url, headers=headers)).json()
        assert bare["configured"] is False and bare["can_manage"] is True

        await _link(c, headers, company["id"], "ga4", "properties/377777674")
        await _link(
            c,
            headers,
            company["id"],
            "gads",
            "3067322982",
            {"manager_id": "8408804299", "currency": "EUR"},
        )
        await c.put(
            "/api/v1/marketing/settings", json={"ads_developer_token": "dev-token"}, headers=headers
        )
        ads.script(
            "FROM customer",
            [
                {
                    "segments": {"date": "2026-08-29"},
                    "metrics": metrics(
                        cost_micros=100_000_000,
                        clicks=80,
                        conversions=6,
                        conversions_value=1500,
                        all_conversions=12,
                        allConversionsValue=1600,
                    ),
                },
                {
                    "segments": {"date": "2026-09-01"},
                    "metrics": metrics(
                        cost_micros=79_030_000,
                        clicks=78,
                        conversions=5,
                        conversions_value=1010,
                        all_conversions=17,
                        allConversionsValue=1110,
                    ),
                },
            ],
        )
        ads.script(
            "search_impression_share",
            [
                {
                    "campaign": {"id": "1", "name": "PMax-verhuizen", "status": "ENABLED"},
                    "metrics": metrics(
                        cost_micros=104_140_000,
                        clicks=127,
                        impressions=882,
                        conversions=10,
                        conversions_value=2380,
                        all_conversions=24,
                        ctr=0.144,
                        conversionsFromInteractionsRate=0.0787,
                        searchImpressionShare=0.0999,
                        searchRankLostImpressionShare=0.859,
                        searchBudgetLostImpressionShare=0.06,
                    ),
                },
                {
                    "campaign": {
                        "id": "2",
                        "name": "Internationaal verhuizen",
                        "status": "ENABLED",
                    },
                    "metrics": metrics(
                        cost_micros=74_890_000,
                        clicks=31,
                        impressions=311,
                        conversions=1,
                        conversions_value=130,
                        all_conversions=5,
                        ctr=0.0997,
                        conversionsFromInteractionsRate=0.032,
                        searchImpressionShare=0.0999,
                        searchBudgetLostImpressionShare=0.306,
                    ),
                },
                {
                    "campaign": {"id": "3", "name": "PMax-autotransport", "status": "PAUSED"},
                    "metrics": metrics(),
                },
            ],
        )
        ads.script(
            "conversion_action_name",
            [
                {
                    "campaign": {"name": "PMax-verhuizen"},
                    "segments": {
                        "conversionActionName": "Offerte - Autotransport",
                        "conversionActionCategory": "REQUEST_QUOTE",
                    },
                    "metrics": {"conversions": 9, "allConversions": 9, "conversionsValue": 2250},
                },
                {
                    "campaign": {"name": "PMax-verhuizen"},
                    "segments": {
                        "conversionActionName": "Telefoon-NL-430",
                        "conversionActionCategory": "CONTACT",
                    },
                    "metrics": {"conversions": 0, "allConversions": 7, "conversionsValue": 0},
                },
            ],
        )

        # The profile is written through the settings write, validated whole.
        bad = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"lead_profile": {"roles": {"nope": []}}},
            headers=headers,
        )
        assert bad.status_code == 422
        assert bad.json()["error"]["fields"] == {
            "lead_profile": "errors.marketing_lead_profile_invalid"
        }
        saved = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"lead_profile": JACHTTRANS},
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["lead_profile"]["roles"]["request"][0]["value"] == "aanvraag_verzonden"
        settings = (
            await c.get(f"/api/v1/marketing/companies/{company['id']}/settings", headers=headers)
        ).json()
        assert settings["lead_profile"]["quote_values"] == ["offerte"]

        # The acceptance period, as a free span in the URL.
        res = await c.get(url, params={"period": "2026-08-29..2026-09-03"}, headers=headers)
        assert res.status_code == 200, res.text
        data = res.json()
        assert data["configured"] and data["ga4_available"] and data["ads_available"]
        assert data["window"] == {
            "start": "2026-08-29",
            "end": "2026-09-03",
            "token": "2026-08-29..2026-09-03",
            "comparable_from": "2026-08-29",
        }
        widgets = {w["key"]: w for w in data["widgets"]}
        assert widgets["requests"]["value"] == 35 and widgets["contacts"]["value"] == 81
        assert widgets["ads_cost"]["value"] == pytest.approx(179.03)
        assert widgets["ads_cost"]["currency"] == "EUR"
        assert (
            widgets["ads_conversions"]["value"] == 11
            and widgets["ads_conversions"]["secondary"] == 29
        )
        assert widgets["ads_cost_per_conversion"]["value"] == pytest.approx(179.03 / 11, abs=0.01)
        assert widgets["ads_conversion_value"]["value"] == 2510
        assert widgets["ads_by_day"]["series"]["bars"] == ["cost"]
        assert sum(widgets["ads_by_day"]["series"]["values"]["cost"]) == pytest.approx(179.03)
        campaigns = {r["key"]: r for r in widgets["ads_campaigns"]["rows"]}
        assert campaigns["1"]["values"]["cost_per_conversion"] == pytest.approx(10.41, abs=0.01)
        # A ratio the API did not send is null, never 0; a campaign with no share at all is
        # left off the impression-share table rather than drawn as three zeros.
        assert campaigns["3"]["values"]["ctr"] is None
        assert [r["key"] for r in widgets["ads_impression_share"]["rows"]] == ["1", "2"]
        assert widgets["ads_impression_share"]["rows"][1]["values"]["rank_lost"] is None
        actions = widgets["ads_conversion_actions"]["rows"]
        assert (
            actions[0]["texts"]["service"] == "autotransport"
            and actions[0]["texts"]["campaign"] == "PMax-verhuizen"
        )
        assert actions[1]["texts"]["service"] == ""
        # Every report is named on the widget it answered.
        assert widgets["funnel"]["reports"] == ["service"]
        assert widgets["ads_campaigns"]["reports"] == ["ads_campaigns"]
        assert data["breakpoints"][0]["text"] == "Meting herzien"
        assert "before_breakpoint" not in {w["code"] for w in data["warnings"]}
        assert (
            data["ga4_deep_link"].startswith("https://analytics.google.com/")
            and "377777674" in data["ga4_deep_link"]
        )
        assert data["refreshed_at"]
        # The GA4 half went out as at most two batches of five.
        assert 1 <= len(google.batches) <= 2 and all(len(b) <= 5 for b in google.batches)
        assert data["unavailable"] and {u["key"] for u in data["unavailable"]} >= {"applications"}
        # The filter controls came from what the period saw.
        controls = {f["dimension"]: f for f in data["filters"]}
        assert {o["key"] for o in controls["service"]["options"]} >= {"autotransport", "overig"}

        # A period that starts before the hard breakpoint is warned about, with the date.
        early = (
            await c.get(url, params={"period": "2026-08-01..2026-09-03"}, headers=headers)
        ).json()
        warning = next(w for w in early["warnings"] if w["code"] == "before_breakpoint")
        assert warning["details"]["date"] == "2026-08-29"

        # A reader's filter narrows every report; an unknown one is refused, naming what exists.
        filtered = await c.get(
            url,
            params={"period": "2026-08-29..2026-09-03", "f": ["service:autotransport"]},
            headers=headers,
        )
        assert filtered.status_code == 200
        assert filtered.json()["filters"][0]["active"] == ["autotransport"]
        last = google.batches[-1][0]
        assert "andGroup" in last["dimensionFilter"]
        refused = await c.get(url, params={"f": ["colour:red"]}, headers=headers)
        assert refused.status_code == 422
        assert refused.json()["error"]["details"]["dimensions"] == [
            "error_reason",
            "form_type",
            "language",
            "page_title",
            "service",
        ]
        malformed = await c.get(url, params={"f": ["service"]}, headers=headers)
        assert malformed.status_code == 422

        # The editor's catalog names what the property carries.
        catalog = (await c.get(f"{url}/catalog", headers=headers)).json()
        assert catalog["ga4_available"] and catalog["key_events"] == ["aanvraag_verzonden"]
        assert {d["parameter"] for d in catalog["custom_dimensions"]} >= {"dienst", "foutreden"}
        assert catalog["custom_dimensions"][0]["field"].startswith("customEvent:")
        assert catalog["events"][0]["name"] == "aanvraag_verzonden"

        # A client login: the same widgets, no diagnostics, no deep links, no unavailable list.
        contact = (
            await c.post(
                "/api/v1/contacts",
                json={
                    "first_name": "Karim",
                    "last_name": "Klant",
                    "email": "karim-leads@example.com",
                    "company_ids": [company["id"]],
                },
                headers=headers,
            )
        ).json()
        await c.post(f"/api/v1/portal/logins/contact/{contact['id']}", headers=headers)
        async with async_session_maker() as session:
            portal_user = await session.scalar(select(User).where(User.email == contact["email"]))
        portal_headers = await auth_cookie(portal_user)
        portal = await c.get(
            url, params={"period": "2026-08-29..2026-09-03"}, headers=portal_headers
        )
        assert portal.status_code == 200, portal.text
        pdata = portal.json()
        assert {w["key"] for w in pdata["widgets"]} == set(widgets)
        assert (
            pdata["unavailable"] == []
            and pdata["ga4_deep_link"] == ""
            and pdata["ads_deep_link"] == ""
        )
        assert pdata["can_manage"] is False
        assert not {w["code"] for w in pdata["warnings"]} & {
            "silent_zero",
            "dimension_unregistered",
            "key_events_mismatch",
        }
        assert (await c.get(f"{url}/catalog", headers=portal_headers)).status_code == 403

        # Removing the profile removes the dashboard, by an explicit null.
        cleared = await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"lead_profile": None},
            headers=headers,
        )
        assert cleared.json()["lead_profile"] is None
        assert (await c.get(url, headers=headers)).json()["configured"] is False


async def test_setup_checks_name_what_the_property_lacks(client_for, fakes) -> None:
    google, _ads, _redis = fakes
    google.parameters = ["dienst"]
    google.key_events = ["purchase"]
    t = await _connected("leads-setup")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Klant"}, headers=headers)
        ).json()
        await _link(c, headers, company["id"], "ga4", "properties/1")
        profile = json.loads(json.dumps(JACHTTRANS))
        profile["ads"]["enabled"] = False
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"lead_profile": profile},
            headers=headers,
        )
        data = (
            await c.get(
                f"/api/v1/marketing/companies/{company['id']}/leads",
                params={"period": "2026-08-29..2026-09-03"},
                headers=headers,
            )
        ).json()
        codes = [w for w in data["warnings"] if w["code"] == "dimension_unregistered"]
        assert {w["details"]["dimension"] for w in codes} == {
            "form_type",
            "language",
            "page_title",
            "error_reason",
        }
        assert any(w["code"] == "key_events_mismatch" for w in data["warnings"])
        assert data["ads_available"] is False
        assert "ads_cost" in {u["key"] for u in data["unavailable"]}


async def test_org_channel_groups_round_trip(client_for) -> None:
    t = await make_tenant("leads-groups")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        before = (await c.get("/api/v1/marketing/settings", headers=headers)).json()
        assert before["channel_groups"] == DEFAULT_CHANNEL_GROUPS
        saved = await c.put(
            "/api/v1/marketing/settings",
            json={
                "channel_groups": {
                    "organic": ["Organic Search"],
                    "ads": ["Paid Search", "Cross-network"],
                    "ai": ["AI Assistant"],
                }
            },
            headers=headers,
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["channel_groups"]["ads"] == ["Paid Search", "Cross-network"]
        refused = await c.put(
            "/api/v1/marketing/settings", json={"channel_groups": {"paid": []}}, headers=headers
        )
        assert refused.status_code == 422


async def test_profiles_never_cross_tenants(client_for) -> None:
    a = await make_tenant("leads-iso-a")
    b = await make_tenant("leads-iso-b")
    ha, hb = await auth_cookie(a.user), await auth_cookie(b.user)
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        company = (await ca.post("/api/v1/companies", json={"name": "A"}, headers=ha)).json()
        await ca.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"lead_profile": APEX},
            headers=ha,
        )
        assert (
            await cb.get(f"/api/v1/marketing/companies/{company['id']}/leads", headers=hb)
        ).status_code == 404
        assert (
            await cb.get(f"/api/v1/marketing/companies/{company['id']}/settings", headers=hb)
        ).status_code == 404
    async with async_session_maker() as session:
        await set_current_org(session, b.org.id)
        assert (await session.execute(select(MarketingLink))).scalars().all() == []
    assert MarketingSource.GA4.value == "ga4"
    assert uuid.UUID(company["id"])


async def test_the_report_section_reads_the_same_service(client_for, fakes) -> None:
    """`marketing.leads` on the registry answers from the dashboard's own read, so a document
    and the screen cannot disagree — and a client without a profile gets no section."""
    from app.modules.marketing.report_sections import _leads
    from app.registry import ReportWindow
    from tests.conftest import default_company  # noqa: F401 — the helper the suite uses

    google, ads, _redis = fakes
    t = await _connected("leads-report")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = (
            await c.post("/api/v1/companies", json={"name": "Jachttrans"}, headers=headers)
        ).json()
        await _link(c, headers, company["id"], "ga4", "properties/377777674")
        window = ReportWindow(
            company_id=uuid.UUID(company["id"]),
            start=START,
            end=END,
            compare_start=None,
            compare_end=None,
            locale="nl",
        )
        session, ctx = await _ctx_for(t)
        try:
            assert await _leads(ctx, window) is None
        finally:
            await session.__aexit__(None, None, None)
        profile = json.loads(json.dumps(JACHTTRANS))
        profile["ads"]["enabled"] = False
        await c.put(
            f"/api/v1/marketing/companies/{company['id']}/settings",
            json={"lead_profile": profile},
            headers=headers,
        )
        session, ctx = await _ctx_for(t)
        try:
            section = await _leads(ctx, window)
        finally:
            await session.__aexit__(None, None, None)
    assert section is not None
    assert section["totals"] == {"requests": 35, "quotes": 21, "contacts": 81, "failures": 21}
    rows = {row["label"]: row for row in section["rows"]}
    assert rows["Internationaal transport"]["requests"] == 2
    assert rows["werken-bij"] == {
        "label": "werken-bij",
        "requests": 0,
        "started": 1,
        "submitted": 0,
        "dropout": 100.0,
    }
    assert section["columns"] == ["requests", "started", "submitted", "dropout"]


async def _ctx_for(t):
    from app.core.permissions import PermissionSet
    from app.core.tenancy import RequestContext

    session = async_session_maker()
    await session.__aenter__()
    await set_current_org(session, t.org.id)
    return session, RequestContext(
        user=t.user, org=t.org, session=session, permissions=PermissionSet.of(["*"])
    )
