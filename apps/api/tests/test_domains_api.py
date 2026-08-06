"""Domains module: CRUD, party/provider validation, DNS refresh, tenant isolation (#90/#92)."""

from __future__ import annotations

from app.modules.domains import service as domains_service
from app.modules.domains.dns import DnsFacts
from tests.conftest import auth_cookie, make_tenant


async def _company(client, headers, name: str = "Acme") -> str:
    r = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    return r.json()["id"]


async def _provider(client, headers, kind: str, name: str) -> str:
    r = await client.post(
        "/api/v1/providers", json={"kind": kind, "name": name}, headers=headers
    )
    return r.json()["id"]


async def test_domain_crud_with_providers_and_party(client_for) -> None:
    t = await make_tenant("dom-crud")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        registrar = await _provider(c, headers, "registrar", "OXXA")
        email_host = await _provider(c, headers, "email", "Google Workspace")

        created = await c.post(
            "/api/v1/domains",
            json={
                "name": "example.nl",
                "company_id": company,
                "registrar_provider_id": registrar,
                "registry_contact": {"type": "agency"},
                "email_enabled": True,
                "email_provider_id": email_host,
                "email_contact": {"type": "agency"},
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        domain = created.json()
        assert domain["name"] == "example.nl"
        assert domain["company_name"] == "Acme"
        assert domain["registrar_provider_name"] == "OXXA"
        # The agency party resolves to the tenant's brand name.
        assert domain["registry_contact"] == {"type": "agency", "id": None, "label": "Dom-Crud"}
        assert domain["email_contact"]["type"] == "agency"

        # Duplicate name in the same tenant → 409.
        dup = await c.post(
            "/api/v1/domains", json={"name": "example.nl", "company_id": company}, headers=headers
        )
        assert dup.status_code == 409

        # Turning email off clears its provider + contact.
        patched = await c.patch(
            f"/api/v1/domains/{domain['id']}", json={"email_enabled": False}, headers=headers
        )
        body = patched.json()
        assert body["email_enabled"] is False
        assert body["email_provider_id"] is None
        assert body["email_contact"] is None


async def test_domain_redirect_url_create_and_update(client_for) -> None:
    t = await make_tenant("dom-redirect")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)

        # Create a redirect domain carrying its target.
        created = await c.post(
            "/api/v1/domains",
            json={
                "name": "oud.nl",
                "company_id": company,
                "status": "redirect",
                "redirect_url": "  https://nieuw.nl  ",
            },
            headers=headers,
        )
        assert created.status_code == 201, created.text
        domain = created.json()
        assert domain["status"] == "redirect"
        # Stored as typed, only trimmed.
        assert domain["redirect_url"] == "https://nieuw.nl"

        # Update the target.
        patched = await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"redirect_url": "https://elders.nl"},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["redirect_url"] == "https://elders.nl"

        # Clearing it (empty string) stores NULL.
        cleared = await c.patch(
            f"/api/v1/domains/{domain['id']}", json={"redirect_url": ""}, headers=headers
        )
        assert cleared.json()["redirect_url"] is None

        # A script-executing scheme is refused at the API boundary.
        bad = await c.patch(
            f"/api/v1/domains/{domain['id']}",
            json={"redirect_url": "javascript:alert(1)"},
            headers=headers,
        )
        assert bad.status_code == 422


async def test_domain_name_normalized_to_root(client_for) -> None:
    """A pasted URL or a habitual "www." stores the bare root domain (schemas.py)."""
    t = await make_tenant("dom-norm")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)

        created = await c.post(
            "/api/v1/domains",
            json={"name": "https://www.Example.NL/pagina?x=1", "company_id": company},
            headers=headers,
        )
        assert created.status_code == 201, created.text
        assert created.json()["name"] == "example.nl"

        # The normalized form collides with what "www.example.nl" reduces to.
        dup = await c.post(
            "/api/v1/domains",
            json={"name": "www.example.nl", "company_id": company},
            headers=headers,
        )
        assert dup.status_code == 409

        # Update normalizes the same way.
        patched = await c.patch(
            f"/api/v1/domains/{created.json()['id']}",
            json={"name": "WWW.Nieuw.NL."},
            headers=headers,
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["name"] == "nieuw.nl"

        # A value that strips to nothing is a validation error, not an empty row.
        empty = await c.post(
            "/api/v1/domains", json={"name": "www.", "company_id": company}, headers=headers
        )
        assert empty.status_code == 422


async def test_domain_rejects_wrong_provider_kind(client_for) -> None:
    t = await make_tenant("dom-kind")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        email_host = await _provider(c, headers, "email", "Exchange")
        # An email provider in the registrar slot is rejected.
        r = await c.post(
            "/api/v1/domains",
            json={"name": "x.nl", "company_id": company, "registrar_provider_id": email_host},
            headers=headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "errors.invalid_provider"


async def test_domain_rejects_cross_tenant_party(client_for) -> None:
    a = await make_tenant("dom-party-a")
    b = await make_tenant("dom-party-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(b.host) as cb:
        await _company(cb, b_headers, "Beta")
        b_contact = (
            await cb.post(
                "/api/v1/contacts", json={"first_name": "Bob"}, headers=b_headers
            )
        ).json()["id"]
    async with client_for(a.host) as ca:
        a_company = await _company(ca, a_headers, "Alpha")
        # A's domain cannot name B's contact as its registry party.
        r = await ca.post(
            "/api/v1/domains",
            json={
                "name": "a.nl",
                "company_id": a_company,
                "registry_contact": {"type": "contact", "id": b_contact},
            },
            headers=a_headers,
        )
        assert r.status_code == 400
        assert r.json()["error"]["message"] == "errors.invalid_party"


async def test_domain_tenant_isolation(client_for) -> None:
    a = await make_tenant("dom-iso-a")
    b = await make_tenant("dom-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        company = await _company(ca, a_headers)
        created = await ca.post(
            "/api/v1/domains", json={"name": "iso.nl", "company_id": company}, headers=a_headers
        )
        domain_id = created.json()["id"]
    async with client_for(b.host) as cb:
        listing = await cb.get("/api/v1/domains", headers=b_headers)
        assert listing.json()["total"] == 0
        assert (await cb.get(f"/api/v1/domains/{domain_id}", headers=b_headers)).status_code == 404


async def test_domain_dns_refresh(client_for, monkeypatch) -> None:
    async def fake_fetch(name: str) -> DnsFacts:
        return DnsFacts(
            nameservers=["ns1.example.net", "ns2.example.net"],
            dnssec=True,
            mx=[
                {"priority": 10, "exchange": "mail1.example.net"},
                {"priority": 20, "exchange": "mail2.example.net"},
            ],
        )

    monkeypatch.setattr(domains_service, "fetch_dns", fake_fetch)

    t = await make_tenant("dom-dns")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        domain = (
            await c.post(
                "/api/v1/domains", json={"name": "dns.nl", "company_id": company}, headers=headers
            )
        ).json()
        assert domain["nameservers"] is None  # not yet checked
        assert domain["mx_records"] is None

        refreshed = await c.post(f"/api/v1/domains/{domain['id']}/refresh", headers=headers)
        assert refreshed.status_code == 200
        body = refreshed.json()
        assert body["nameservers"] == ["ns1.example.net", "ns2.example.net"]
        assert body["dnssec"] is True
        assert body["mx_records"] == [
            {"priority": 10, "exchange": "mail1.example.net"},
            {"priority": 20, "exchange": "mail2.example.net"},
        ]
        assert body["dns_checked_at"] is not None


async def test_list_filters_narrow_the_register(client_for) -> None:
    """The register's filter bar, at the only layer that can answer it (docs/PERFORMANCE.md).

    A filter applied in the browser narrows the fifty rows that happened to load and reports a
    total counted over all of them — so every one of these is a query parameter, and what comes
    back is the *filtered* total.
    """
    t = await make_tenant("dom-filters")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        acme = await _company(c, headers, "Acme")
        other = await _company(c, headers, "Bravo")
        oxxa = await _provider(c, headers, "registrar", "OXXA")
        transip = await _provider(c, headers, "registrar", "TransIP")
        flare = await _provider(c, headers, "dns", "Cloudflare")

        for name, company, status, registrar, dns in [
            ("acme-live.nl", acme, "active", oxxa, flare),
            ("acme-parked.nl", acme, "parked", oxxa, None),
            ("acme-old.nl", acme, "expired", transip, None),
            ("bravo-live.nl", other, "active", transip, flare),
        ]:
            body = {
                "name": name,
                "company_id": company,
                "status": status,
                "registrar_provider_id": registrar,
                "dns_provider_id": dns,
            }
            created = await c.post("/api/v1/domains", json=body, headers=headers)
            assert created.status_code == 201, created.text

        async def names(query: str) -> list[str]:
            res = await c.get(f"/api/v1/domains?{query}", headers=headers)
            assert res.status_code == 200, res.text
            body = res.json()
            # The count above a filtered list counts the filter, never the whole table.
            assert body["total"] == len(body["items"])
            return sorted(d["name"] for d in body["items"])

        assert await names(f"company_id={acme}") == [
            "acme-live.nl",
            "acme-old.nl",
            "acme-parked.nl",
        ]
        assert await names("status=active") == ["acme-live.nl", "bravo-live.nl"]
        assert await names(f"registrar_provider_id={oxxa}") == ["acme-live.nl", "acme-parked.nl"]
        assert await names(f"dns_provider_id={flare}") == ["acme-live.nl", "bravo-live.nl"]
        assert await names("q=acme") == ["acme-live.nl", "acme-old.nl", "acme-parked.nl"]
        # Filters compose — the bar sets several at once and each narrows the last.
        assert await names(f"company_id={acme}&status=active&registrar_provider_id={oxxa}") == [
            "acme-live.nl"
        ]
        # A combination that matches nothing says so, rather than falling back to everything.
        assert await names(f"company_id={other}&status=expired") == []


async def test_company_panel_shows_five_domains_and_the_whole_count(client_for) -> None:
    """The client card is the first page of the register, and it says how long that register is.

    Showing five without the count is the truncated-total failure (#37) in miniature: a card
    listing five for a client who has eight reads as the complete answer, and nothing on screen
    contradicts it.
    """
    t = await make_tenant("dom-panel-cap")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        for i in range(8):
            created = await c.post(
                "/api/v1/domains",
                json={"name": f"klant-{i:02d}.nl", "company_id": company},
                headers=headers,
            )
            assert created.status_code == 201, created.text

        res = await c.get(f"/api/v1/companies/{company}/panels", headers=headers)
        panel = next(p for p in res.json() if p["key"] == "domains.company")["data"]
        assert panel["total"] == 8
        assert len(panel["domains"]) == 5
        # The order the list itself opens in (name ascending), so "view all" continues the card
        # rather than reshuffling it.
        assert [d["name"] for d in panel["domains"]] == [f"klant-{i:02d}.nl" for i in range(5)]
