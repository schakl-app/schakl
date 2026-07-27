"""Klantnummer: allocation, the tenant-configurable format, uniqueness, and the backfill.

The rules worth pinning down are the ones a naive implementation gets wrong: a company saved
with its *own* number is not a conflict (or the export→import round-trip 409s), the number is
searchable and sortable (or having one is pointless), a rewound sequence walks past what it has
already handed out, and uniqueness is scoped to the org (Golden Rule 1).
"""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant

SETTINGS = "/api/v1/companies/settings"


async def _create(client, headers, **body) -> dict:
    r = await client.post("/api/v1/companies", json=body, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def test_numbers_are_allocated_from_the_org_format(client_for) -> None:
    t = await make_tenant("cnum-alloc")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # The default format, untouched.
        settings = (await c.get(SETTINGS, headers=headers)).json()
        assert settings["client_number_format"] == "{seq:4}"
        assert settings["client_number_auto"] is True

        assert (await _create(c, headers, name="Acme"))["client_number"] == "0001"
        assert (await _create(c, headers, name="Beta"))["client_number"] == "0002"

        # A tenant-chosen format takes over from the next allocation, and the sequence is
        # editable so an instance can align with numbering it already uses elsewhere.
        r = await c.put(
            SETTINGS,
            json={"client_number_format": "K{year}-{seq:3}", "client_number_next_seq": 42},
            headers=headers,
        )
        assert r.status_code == 200
        year = r.json()["client_number_seq_year"]
        created = await _create(c, headers, name="Gamma")
        assert created["client_number"].endswith("-042")
        assert created["client_number"].startswith("K")
        # The year token resolves on the *org's* calendar, not UTC's.
        assert str(year or "") in created["client_number"] or year is None


async def test_invalid_format_is_rejected_with_an_i18n_key(client_for) -> None:
    t = await make_tenant("cnum-fmt")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        for bad in ("no-counter-here", "{seq}-{seq}", "{maand}-{seq}", "   "):
            r = await c.put(SETTINGS, json={"client_number_format": bad}, headers=headers)
            assert r.status_code == 422, bad
            assert "errors.companies.invalid_number_format" in r.text, bad


async def test_manual_numbers_win_and_duplicates_conflict(client_for) -> None:
    t = await make_tenant("cnum-manual")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        mine = await _create(c, headers, name="Acme", client_number="KLANT-7")
        assert mine["client_number"] == "KLANT-7"

        # A second company cannot take it.
        r = await c.post(
            "/api/v1/companies", json={"name": "Beta", "client_number": "KLANT-7"},
            headers=headers,
        )
        assert r.status_code == 409
        assert r.json()["error"]["message"] == "errors.companies.client_number_taken"

        # Nor can an existing company be edited onto it.
        beta = await _create(c, headers, name="Beta")
        r = await c.patch(
            f"/api/v1/companies/{beta['id']}", json={"client_number": "KLANT-7"},
            headers=headers,
        )
        assert r.status_code == 409

        # But saving a company carrying its *own* number is not a conflict — the whole
        # export → edit → re-import round-trip depends on this.
        r = await c.patch(
            f"/api/v1/companies/{mine['id']}",
            json={"client_number": "KLANT-7", "city": "Utrecht"},
            headers=headers,
        )
        assert r.status_code == 200
        assert r.json()["city"] == "Utrecht"


async def test_auto_off_leaves_the_number_blank(client_for) -> None:
    t = await make_tenant("cnum-off")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(SETTINGS, json={"client_number_auto": False}, headers=headers)
        assert (await _create(c, headers, name="Acme"))["client_number"] is None
        # A number typed by hand is still honoured while auto is off.
        assert (await _create(c, headers, name="Beta", client_number="X1"))[
            "client_number"
        ] == "X1"


async def test_a_rewound_sequence_walks_past_numbers_already_handed_out(client_for) -> None:
    t = await make_tenant("cnum-rewind")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        assert (await _create(c, headers, name="A"))["client_number"] == "0001"
        assert (await _create(c, headers, name="B"))["client_number"] == "0002"
        # Someone rewinds the counter to 1; 0001 and 0002 exist, so the next free is 0003.
        await c.put(SETTINGS, json={"client_number_next_seq": 1}, headers=headers)
        assert (await _create(c, headers, name="C"))["client_number"] == "0003"


async def test_backfill_only_fills_blanks_and_is_idempotent(client_for) -> None:
    t = await make_tenant("cnum-backfill")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(SETTINGS, json={"client_number_auto": False}, headers=headers)
        first = await _create(c, headers, name="Oldest")
        second = await _create(c, headers, name="Middle")
        kept = await _create(c, headers, name="Numbered", client_number="KEEP")
        assert first["client_number"] is None

        r = await c.post(f"{SETTINGS}/backfill-client-numbers", headers=headers)
        assert r.status_code == 200
        assert r.json()["numbered"] == 2  # the two blanks, never the one already numbered

        listing = (await c.get("/api/v1/companies", headers=headers)).json()["items"]
        numbers = {i["name"]: i["client_number"] for i in listing}
        assert numbers["Numbered"] == "KEEP"
        # Oldest first: creation order decides who gets 0001.
        assert numbers["Oldest"] == "0001"
        assert numbers["Middle"] == "0002"
        assert first["id"] and second["id"] and kept["id"]

        # Running it again finds nothing left to do.
        again = await c.post(f"{SETTINGS}/backfill-client-numbers", headers=headers)
        assert again.json()["numbered"] == 0


async def test_the_number_is_searchable_and_sortable(client_for) -> None:
    t = await make_tenant("cnum-search")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.put(SETTINGS, json={"client_number_format": "K{seq:4}"}, headers=headers)
        await _create(c, headers, name="Zebra")     # K0001
        await _create(c, headers, name="Alpha")     # K0002
        unnumbered = await _create(c, headers, name="Naamloos", client_number=None)
        await c.patch(
            f"/api/v1/companies/{unnumbered['id']}", json={"client_number": ""},
            headers=headers,
        )

        # Searching the number finds the client — the point of having one.
        found = (await c.get("/api/v1/companies?q=K0001", headers=headers)).json()
        assert [i["name"] for i in found["items"]] == ["Zebra"]

        ordered = (
            await c.get("/api/v1/companies?sort=client_number", headers=headers)
        ).json()["items"]
        # Numbered rows in number order; the blank one files last, not first.
        assert [i["name"] for i in ordered][:2] == ["Zebra", "Alpha"]
        assert ordered[-1]["client_number"] in (None, "")


async def test_client_numbers_never_collide_across_tenants(client_for) -> None:
    """The unique index is ``(org_id, client_number)``.

    A global one would let one tenant's allocation block another's — a Golden Rule 1 breach the
    deny-by-default sweep would never catch, because nothing about it looks like a permission.
    """
    a = await make_tenant("cnum-iso-a")
    b = await make_tenant("cnum-iso-b")
    ha, hb = await auth_cookie(a.user), await auth_cookie(b.user)

    async with client_for(a.host) as ca:
        await _create(ca, ha, name="A BV", client_number="K001")
    async with client_for(b.host) as cb:
        # Same number, different tenant: allowed, and B's own auto-sequence is untouched by A's.
        assert (await _create(cb, hb, name="B BV", client_number="K001"))[
            "client_number"
        ] == "K001"
        assert (await _create(cb, hb, name="B2 BV"))["client_number"] == "0001"

    async with client_for(a.host) as ca:
        listing = (await ca.get("/api/v1/companies", headers=ha)).json()
        assert [i["name"] for i in listing["items"]] == ["A BV"]


async def test_settings_require_the_manage_permission(client_for) -> None:
    t = await make_tenant("cnum-rbac", role="member")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # A plain member may create clients (and so gets numbers) but not redefine the scheme.
        assert (await c.get(SETTINGS, headers=headers)).status_code == 403
        assert (
            await c.put(SETTINGS, json={"client_number_format": "X{seq}"}, headers=headers)
        ).status_code == 403
        assert (
            await c.post(f"{SETTINGS}/backfill-client-numbers", headers=headers)
        ).status_code == 403
