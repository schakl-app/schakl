"""A hosting agreement keeps a website online, and the website can now say which one.

``subscription_links`` gained ``website`` beside ``project`` and ``task``; whether a kind of
agreement *may* cover a website is the type's own flag (``covers_websites``, seeded on for
``hosting``), asked when a website link is written and never again. The per-record twins
(``POST /links``, ``DELETE /links/…``) are what a website's panel attaches through, and
``linkable=true`` is the picker's shortlist — the record's own client, a covering kind, not
yet linked. One test per rule.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant, org_today


async def _company(client, headers, name: str = "Klant BV") -> str:
    resp = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _website(client, headers, company_id: str, name: str, *, root: bool = True) -> str:
    resp = await client.post(
        "/api/v1/domains", json={"name": name, "company_id": company_id}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    resp = await client.post(
        "/api/v1/websites", json={"domain_id": resp.json()["id"], "root": root}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


async def _type(client, headers, key: str) -> dict:
    types = (await client.get("/api/v1/subscriptions/types", headers=headers)).json()
    return next(row for row in types if row["key"] == key)


async def _subscription(client, headers, company_id: str, type_id: str | None, **extra) -> dict:
    resp = await client.post(
        "/api/v1/subscriptions",
        json={
            "name": extra.pop("name", "Hosting Pro"),
            "company_id": company_id,
            "subscription_type_id": type_id,
            "interval": "yearly",
            "amount": "155.00",
            "start_date": org_today().isoformat(),
            **extra,
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_the_seeded_hosting_type_covers_websites_and_marketing_does_not(client_for) -> None:
    t = await make_tenant("weblink-seed")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await _type(c, headers, "hosting"))["covers_websites"] is True
        assert (await _type(c, headers, "marketing"))["covers_websites"] is False


async def test_a_website_link_needs_a_kind_that_covers_websites(client_for) -> None:
    """The flag is the type's decision: a marketing retainer and an untyped agreement both
    refuse, naming the field, and the refusal reaches the form's save and the panel's attach
    alike — one gate, two doors."""
    t = await make_tenant("weblink-gate")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        site = await _website(c, headers, company, "klant.nl")
        marketing = await _type(c, headers, "marketing")
        link = {"entity_type": "website", "entity_id": site}

        refused = await c.post(
            "/api/v1/subscriptions",
            json={
                "name": "SEO",
                "company_id": company,
                "subscription_type_id": marketing["id"],
                "amount": "300.00",
                "start_date": org_today().isoformat(),
                "links": [link],
            },
            headers=headers,
        )
        assert refused.status_code == 422, refused.text
        assert refused.json()["error"]["fields"] == {
            "links": "errors.subscriptions_type_no_websites"
        }

        seo = await _subscription(c, headers, company, marketing["id"], name="SEO")
        attach = await c.post(
            f"/api/v1/subscriptions/{seo['id']}/links", json=link, headers=headers
        )
        assert attach.status_code == 422, attach.text

        untyped = await _subscription(c, headers, company, None, name="Losse afspraak")
        attach = await c.post(
            f"/api/v1/subscriptions/{untyped['id']}/links", json=link, headers=headers
        )
        assert attach.status_code == 422, attach.text


async def test_attach_list_by_website_and_detach(client_for) -> None:
    t = await make_tenant("weblink-attach")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        site = await _website(c, headers, company, "klant.nl", root=False)
        hosting = await _type(c, headers, "hosting")
        sub = await _subscription(c, headers, company, hosting["id"])

        attached = await c.post(
            f"/api/v1/subscriptions/{sub['id']}/links",
            json={"entity_type": "website", "entity_id": site},
            headers=headers,
        )
        assert attached.status_code == 201, attached.text
        links = attached.json()["links"]
        # The link carries what the website is called: the host it answers on, `www` included.
        assert [(row["entity_type"], row["entity_id"], row["label"]) for row in links] == [
            ("website", site, "www.klant.nl")
        ]

        # Attaching twice is one link — the panel's button may be pressed twice.
        again = await c.post(
            f"/api/v1/subscriptions/{sub['id']}/links",
            json={"entity_type": "website", "entity_id": site},
            headers=headers,
        )
        assert again.status_code == 201
        assert len(again.json()["links"]) == 1

        # The website's panel question: which agreements cover this site?
        covering = (
            await c.get(
                "/api/v1/subscriptions",
                params={"entity_type": "website", "entity_id": site},
                headers=headers,
            )
        ).json()
        assert [row["id"] for row in covering["items"]] == [sub["id"]]

        # Once attached, it is off the shortlist of what *could* be attached.
        linkable = (
            await c.get(
                "/api/v1/subscriptions",
                params={"entity_type": "website", "entity_id": site, "linkable": "true"},
                headers=headers,
            )
        ).json()
        assert linkable["items"] == []

        # The trail names what was attached, so "why is this site on that agreement" has an
        # answer a month later.
        trail = (
            await c.get(
                "/api/v1/activity",
                params={"entity_type": "subscription", "entity_id": sub["id"]},
                headers=headers,
            )
        ).json()
        linked = [row for row in trail if row["action"] == "linked"]
        assert linked and linked[0]["payload"]["label"] == "www.klant.nl"

        gone = await c.delete(
            f"/api/v1/subscriptions/{sub['id']}/links/website/{site}", headers=headers
        )
        assert gone.status_code == 204, gone.text
        assert (await c.get(f"/api/v1/subscriptions/{sub['id']}", headers=headers)).json()[
            "links"
        ] == []
        # Detaching what is not attached names nothing this agreement covers.
        assert (
            await c.delete(
                f"/api/v1/subscriptions/{sub['id']}/links/website/{site}", headers=headers
            )
        ).status_code == 404


async def test_linkable_is_the_websites_client_covering_kinds_and_nothing_else(client_for) -> None:
    """The picker's shortlist: another client's hosting, this client's marketing retainer and a
    cancelled hosting agreement are all real rows and none of them is offered."""
    t = await make_tenant("weblink-shortlist")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers, "Klant BV")
        other = await _company(c, headers, "Ander BV")
        site = await _website(c, headers, company, "klant.nl")
        hosting = await _type(c, headers, "hosting")
        marketing = await _type(c, headers, "marketing")

        offered = await _subscription(c, headers, company, hosting["id"], name="Hosting Pro")
        await _subscription(c, headers, other, hosting["id"], name="Andermans hosting")
        await _subscription(c, headers, company, marketing["id"], name="SEO")
        await _subscription(
            c, headers, company, hosting["id"], name="Oude hosting", status="cancelled"
        )

        linkable = (
            await c.get(
                "/api/v1/subscriptions",
                params={"entity_type": "website", "entity_id": site, "linkable": "true"},
                headers=headers,
            )
        ).json()
        assert [row["name"] for row in linkable["items"]] == [offered["name"]]
        assert linkable["total"] == 1


async def test_a_tenants_own_flag_widens_what_may_cover_a_website(client_for) -> None:
    """"Onderhoud" ticked in Instellingen makes a maintenance agreement attachable too — the
    rule is the flag, never the seeded key."""
    t = await make_tenant("weblink-flag")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        company = await _company(c, headers)
        site = await _website(c, headers, company, "klant.nl")
        onderhoud = await _type(c, headers, "onderhoud")
        sub = await _subscription(c, headers, company, onderhoud["id"], name="Onderhoud")
        link = {"entity_type": "website", "entity_id": site}

        refused = await c.post(
            f"/api/v1/subscriptions/{sub['id']}/links", json=link, headers=headers
        )
        assert refused.status_code == 422

        flipped = await c.patch(
            f"/api/v1/subscriptions/types/{onderhoud['id']}",
            json={"covers_websites": True},
            headers=headers,
        )
        assert flipped.status_code == 200, flipped.text
        assert flipped.json()["covers_websites"] is True
        attached = await c.post(
            f"/api/v1/subscriptions/{sub['id']}/links", json=link, headers=headers
        )
        assert attached.status_code == 201, attached.text

        # A stored link survives the flag being switched off again, and the form's whole-set
        # save — which re-posts it — is not refused on its account (#335: gated when written).
        await c.patch(
            f"/api/v1/subscriptions/types/{onderhoud['id']}",
            json={"covers_websites": False},
            headers=headers,
        )
        resaved = await c.patch(
            f"/api/v1/subscriptions/{sub['id']}",
            json={"name": "Onderhoud Plus", "links": [link]},
            headers=headers,
        )
        assert resaved.status_code == 200, resaved.text
        assert [row["entity_id"] for row in resaved.json()["links"]] == [site]


async def test_another_tenants_website_cannot_be_linked_or_shortlisted(client_for) -> None:
    a = await make_tenant("weblink-a")
    b = await make_tenant("weblink-b")
    headers_a = await auth_cookie(a.user)
    headers_b = await auth_cookie(b.user)
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        company_a = await _company(ca, headers_a)
        site_a = await _website(ca, headers_a, company_a, "klant-a.nl")
        company_b = await _company(cb, headers_b)
        hosting_b = await _type(cb, headers_b, "hosting")
        sub_b = await _subscription(cb, headers_b, company_b, hosting_b["id"])

        crossed = await cb.post(
            f"/api/v1/subscriptions/{sub_b['id']}/links",
            json={"entity_type": "website", "entity_id": site_a},
            headers=headers_b,
        )
        assert crossed.status_code == 400, crossed.text
        assert crossed.json()["error"]["fields"] == {"links": "errors.not_found"}

        shortlist = (
            await cb.get(
                "/api/v1/subscriptions",
                params={"entity_type": "website", "entity_id": site_a, "linkable": "true"},
                headers=headers_b,
            )
        ).json()
        assert shortlist["items"] == [] and shortlist["total"] == 0
