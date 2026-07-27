"""Address lookup (#241): PDOK parsing, fail-soft on a down provider, and the permission gate.

No table ships with this — the endpoint holds no tenant data, so there is no isolation test to
write; ``require_context`` still binds the org and the deny-by-default sweep covers the route.
"""

from __future__ import annotations

import httpx

from app.core.addresslookup import providers as addresslookup_providers
from app.core.addresslookup.providers import AddressSuggestion, PdokProvider, _parse_pdok
from tests.conftest import auth_cookie, make_tenant

PDOK_PAYLOAD = {
    "response": {
        "numFound": 1,
        "docs": [
            {
                "straatnaam": "Binnenhof",
                "huis_nlt": "1A",
                "postcode": "2513AA",
                "woonplaatsnaam": "'s-Gravenhage",
            }
        ],
    }
}


def test_parse_pdok_maps_docs_to_suggestions() -> None:
    suggestions = _parse_pdok(PDOK_PAYLOAD)
    assert suggestions == [
        AddressSuggestion(
            street="Binnenhof",
            house_number="1A",
            postal_code="2513AA",
            city="'s-Gravenhage",
            country="NL",
        )
    ]


def test_parse_pdok_tolerates_garbage() -> None:
    # Malformed payloads and incomplete docs are silence, never an exception.
    assert _parse_pdok(None) == []
    assert _parse_pdok({"response": {"docs": "nope"}}) == []
    assert _parse_pdok({"response": {"docs": [{"postcode": "2513AA"}]}}) == []


async def test_pdok_provider_parses_a_wire_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # Fielded, not free text; the numeric part queries, the letter is matched after.
        assert request.url.params["q"] == "postcode:2513AA and huisnummer:1"
        assert request.url.params["fq"] == "type:adres"
        return httpx.Response(200, json=PDOK_PAYLOAD)

    provider = PdokProvider(transport=httpx.MockTransport(handler))
    suggestions = await provider.lookup("2513 aa", "1A")
    assert [s.street for s in suggestions] == ["Binnenhof"]


async def test_pdok_provider_prefers_the_typed_house_number() -> None:
    docs = [
        {
            "straatnaam": "Binnenhof",
            "huis_nlt": n,
            "postcode": "2513AA",
            "woonplaatsnaam": "Den Haag",
        }
        for n in ("1", "1A", "1B")
    ]
    payload = {"response": {"docs": docs}}
    provider = PdokProvider(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    )
    suggestions = await provider.lookup("2513AA", "1a")
    assert [s.house_number for s in suggestions] == ["1A", "1", "1B"]


async def test_pdok_provider_fails_soft() -> None:
    # A 500 and a network error both read as "no suggestion", never raise.
    provider = PdokProvider(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    assert await provider.lookup("2513AA", "1") == []

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    provider = PdokProvider(transport=httpx.MockTransport(boom))
    assert await provider.lookup("2513AA", "1") == []


async def test_pdok_provider_skips_non_dutch_postcodes() -> None:
    # PDOK only covers NL: an impossible postcode is answered locally, no network call.
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not hit the network")

    provider = PdokProvider(transport=httpx.MockTransport(handler))
    assert await provider.lookup("SW1A 1AA", "10") == []
    assert await provider.lookup("2513AA", "   ") == []


class _FakeProvider:
    async def lookup(self, postal_code: str, house_number: str) -> list[AddressSuggestion]:
        return [
            AddressSuggestion(
                street="Teststraat",
                house_number=house_number,
                postal_code=postal_code.replace(" ", "").upper(),
                city="Testdorp",
                country="NL",
            )
        ]


async def test_lookup_endpoint_returns_suggestions(client_for, monkeypatch) -> None:
    monkeypatch.setattr("app.core.addresslookup.router.get_provider", lambda: _FakeProvider())
    t = await make_tenant("addr-ok")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        resp = await c.get(
            "/api/v1/addresslookup",
            params={"postal_code": "1234 ab", "house_number": "10"},
            headers=headers,
        )
    assert resp.status_code == 200
    assert resp.json() == {
        "suggestions": [
            {
                "street": "Teststraat",
                "house_number": "10",
                "postal_code": "1234AB",
                "city": "Testdorp",
                "country": "NL",
            }
        ]
    }


async def test_member_holds_the_lookup_permission_by_default(client_for, monkeypatch) -> None:
    monkeypatch.setattr("app.core.addresslookup.router.get_provider", lambda: _FakeProvider())
    t = await make_tenant("addr-member", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        resp = await c.get(
            "/api/v1/addresslookup",
            params={"postal_code": "1234AB", "house_number": "10"},
            headers=headers,
        )
    assert resp.status_code == 200


async def test_client_role_is_refused(client_for) -> None:
    # A portal login edits no addresses; the default seeding leaves the permission off.
    t = await make_tenant("addr-client", role="client")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        resp = await c.get(
            "/api/v1/addresslookup",
            params={"postal_code": "1234AB", "house_number": "10"},
            headers=headers,
        )
    assert resp.status_code == 403


def test_provider_seam_defaults_to_pdok() -> None:
    assert isinstance(addresslookup_providers.get_provider(), PdokProvider)
