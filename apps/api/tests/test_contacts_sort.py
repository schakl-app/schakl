"""Server-side sorting for the contact-person list (#39).

The interesting one is `sort=company`. A contact links to *many* companies, and `is_primary` on
the link means "the primary contact **for that company**" — unique per company, not per contact —
so "their primary company" does not exist: the same person can be primary at three clients. The
sort therefore takes the alphabetically first linked company, via a correlated subquery. A join
would multiply the contact's row and change which contacts land on the page.
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def _contact(client, headers, first: str, last: str | None = None, **extra) -> str:
    res = await client.post(
        "/api/v1/contacts",
        json={"first_name": first, "last_name": last, **extra},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _company(client, headers, name: str) -> str:
    res = await client.post("/api/v1/companies", json={"name": name}, headers=headers)
    assert res.status_code == 201, res.text
    return res.json()["id"]


async def _link(client, headers, contact_id: str, company_id: str, primary: bool = False) -> None:
    res = await client.post(
        f"/api/v1/contacts/{contact_id}/links",
        json={"company_id": company_id, "is_primary": primary},
        headers=headers,
    )
    assert res.status_code == 201, res.text


async def test_sorting_names_is_case_insensitive(client_for) -> None:
    t = await make_tenant("cs-name")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        for first in ("Charlie", "alpha", "Bravo"):
            await _contact(c, headers, first)

        asc = (await c.get("/api/v1/contacts?sort=first_name", headers=headers)).json()
        assert [i["first_name"] for i in asc["items"]] == ["alpha", "Bravo", "Charlie"]

        desc = (await c.get("/api/v1/contacts?sort=-first_name", headers=headers)).json()
        assert [i["first_name"] for i in desc["items"]] == ["Charlie", "Bravo", "alpha"]


async def test_unknown_sort_key_is_refused(client_for) -> None:
    t = await make_tenant("cs-bad")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        bad = await c.get("/api/v1/contacts?sort=hashed_password", headers=headers)
        assert bad.status_code == 400
        assert bad.json()["error"]["message"] == "errors.invalid_sort"


async def test_missing_values_sort_last_in_both_directions(client_for) -> None:
    t = await make_tenant("cs-nulls")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await _contact(c, headers, "Has", "Job", job_title="Developer")
        await _contact(c, headers, "No", "Job")

        for sort in ("job_title", "-job_title"):
            items = (await c.get(f"/api/v1/contacts?sort={sort}", headers=headers)).json()["items"]
            assert items[-1]["first_name"] == "No", sort


async def test_sorting_by_company_takes_the_alphabetically_first_link(client_for) -> None:
    t = await make_tenant("cs-company")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        acme = await _company(c, headers, "Acme")
        zenith = await _company(c, headers, "Zenith")

        # Ann is linked to both, and (being each company's first contact) is primary at both —
        # which is exactly why "their primary company" is not a usable sort key. She sorts "Acme".
        ann = await _contact(c, headers, "Ann")
        await _link(c, headers, ann, zenith)
        await _link(c, headers, ann, acme)

        bob = await _contact(c, headers, "Bob")
        await _link(c, headers, bob, zenith)

        # Linked to nobody: sorts last.
        await _contact(c, headers, "Cleo")

        page = (await c.get("/api/v1/contacts?sort=company", headers=headers)).json()
        assert [i["first_name"] for i in page["items"]] == ["Ann", "Bob", "Cleo"]
        # The multi-linked contact appears exactly once — a join would have duplicated her.
        assert page["total"] == 3
        assert len(page["items"]) == 3


async def test_sorting_by_company_never_duplicates_a_multi_company_contact(client_for) -> None:
    t = await make_tenant("cs-dup")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        contact = await _contact(c, headers, "Busy")
        for name in ("A", "B", "C"):
            await _link(c, headers, contact, await _company(c, headers, name))

        page = (await c.get("/api/v1/contacts?sort=company", headers=headers)).json()
        assert len(page["items"]) == 1
        assert page["total"] == 1


async def test_contact_sort_never_crosses_tenants(client_for) -> None:
    a = await make_tenant("cs-iso-a")
    b = await make_tenant("cs-iso-b")
    async with client_for(a.host) as c:
        headers = await auth_cookie(a.user)
        await _contact(c, headers, "AnneFromA")
    async with client_for(b.host) as c:
        headers = await auth_cookie(b.user)
        page = (await c.get("/api/v1/contacts?sort=company", headers=headers)).json()
        assert page["items"] == []
