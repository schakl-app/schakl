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
