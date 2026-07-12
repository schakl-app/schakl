"""Dashboard prefs (user layout + org template) and white-label branding endpoint."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant
from tests.test_task_subresources import add_member


async def test_dashboard_prefs_fallback_chain(client_for) -> None:
    t = await make_tenant("dash-prefs")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        # No layout anywhere → none.
        prefs = (await c.get("/api/v1/dashboard/prefs", headers=member_headers)).json()
        assert prefs == {"widgets": None, "source": "none"}

        # Members may not set the org template…
        assert (
            await c.put(
                "/api/v1/dashboard/prefs/default",
                json={"widgets": ["tasks.my_open"]},
                headers=member_headers,
            )
        ).status_code == 403
        # …a manager may; members inherit it.
        await c.put(
            "/api/v1/dashboard/prefs/default",
            json={"widgets": ["tasks.my_open"]},
            headers=owner_headers,
        )
        prefs = (await c.get("/api/v1/dashboard/prefs", headers=member_headers)).json()
        assert prefs == {"widgets": ["tasks.my_open"], "source": "default"}

        # A personal layout overrides the template…
        await c.put(
            "/api/v1/dashboard/prefs",
            json={"widgets": ["time.today", "tasks.my_open"]},
            headers=member_headers,
        )
        prefs = (await c.get("/api/v1/dashboard/prefs", headers=member_headers)).json()
        assert prefs["source"] == "user"
        assert prefs["widgets"] == ["time.today", "tasks.my_open"]

        # …and resetting falls back to the template again.
        assert (
            await c.delete("/api/v1/dashboard/prefs", headers=member_headers)
        ).status_code == 204
        prefs = (await c.get("/api/v1/dashboard/prefs", headers=member_headers)).json()
        assert prefs["source"] == "default"


async def test_dashboard_prefs_tenant_isolation(client_for) -> None:
    a = await make_tenant("dash-iso-a")
    b = await make_tenant("dash-iso-b")
    async with client_for(a.host) as ca:
        await ca.put(
            "/api/v1/dashboard/prefs/default",
            json={"widgets": ["time.today"]},
            headers=await auth_cookie(a.user),
        )
    async with client_for(b.host) as cb:
        prefs = (
            await cb.get("/api/v1/dashboard/prefs", headers=await auth_cookie(b.user))
        ).json()
        assert prefs["source"] == "none"


async def test_branding_update_manager_gated(client_for) -> None:
    t = await make_tenant("brand")
    owner_headers = await auth_cookie(t.user)
    member = await add_member(t)
    member_headers = await auth_cookie(member)

    async with client_for(t.host) as c:
        assert (
            await c.patch(
                "/api/v1/meta/tenant", json={"brand_name": "Nope"}, headers=member_headers
            )
        ).status_code == 403

        updated = await c.patch(
            "/api/v1/meta/tenant",
            json={
                "brand_name": "Breik",
                "primary_color": "#112233",
                "logo_url": "https://cdn.example.com/logo.svg",
            },
            headers=owner_headers,
        )
        assert updated.status_code == 200
        body = updated.json()
        assert body["brand_name"] == "Breik"
        assert body["primary_color"] == "#112233"

        # Bad colors are rejected; empty logo_url clears it.
        assert (
            await c.patch(
                "/api/v1/meta/tenant",
                json={"primary_color": "red"},
                headers=owner_headers,
            )
        ).status_code == 422
        cleared = await c.patch(
            "/api/v1/meta/tenant", json={"logo_url": ""}, headers=owner_headers
        )
        assert cleared.json()["logo_url"] is None

        # The public branding endpoint reflects the change.
        public = (await c.get("/api/v1/meta/tenant")).json()
        assert public["brand_name"] == "Breik"


async def test_branding_currency_setting(client_for) -> None:
    """Currency is a per-org setting like the timezone (#124): defaults to EUR, validated
    against ISO 4217, case-normalised, and reflected on the public branding payload."""
    t = await make_tenant("brand-cur")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        # Default before anyone touches it.
        assert (await c.get("/api/v1/meta/tenant")).json()["currency"] == "EUR"

        # A typo is rejected with a field error.
        bad = await c.patch(
            "/api/v1/meta/tenant", json={"currency": "EUO"}, headers=owner_headers
        )
        assert bad.status_code == 422

        # Lowercase input is normalised; the public payload reflects the change.
        updated = await c.patch(
            "/api/v1/meta/tenant", json={"currency": "usd"}, headers=owner_headers
        )
        assert updated.status_code == 200
        assert updated.json()["currency"] == "USD"
        assert (await c.get("/api/v1/meta/tenant")).json()["currency"] == "USD"


async def test_branding_tab_title_template(client_for) -> None:
    """The tab-title template (#97): whitelisted tokens only, {page} required, empty clears."""
    t = await make_tenant("brand-tab")
    owner_headers = await auth_cookie(t.user)

    async with client_for(t.host) as c:
        # Default: unset.
        assert (await c.get("/api/v1/meta/tenant")).json()["tab_title_template"] is None

        # {page} is required; unknown tokens are typos, not variables.
        for bad in ("{brand} only", "{page} · {typo}"):
            res = await c.patch(
                "/api/v1/meta/tenant",
                json={"tab_title_template": bad},
                headers=owner_headers,
            )
            assert res.status_code == 422, bad

        updated = await c.patch(
            "/api/v1/meta/tenant",
            json={"tab_title_template": "{page} · {brand}"},
            headers=owner_headers,
        )
        assert updated.status_code == 200
        assert updated.json()["tab_title_template"] == "{page} · {brand}"
        assert (await c.get("/api/v1/meta/tenant")).json()[
            "tab_title_template"
        ] == "{page} · {brand}"

        # Empty string clears it back to the built-in format.
        cleared = await c.patch(
            "/api/v1/meta/tenant", json={"tab_title_template": ""}, headers=owner_headers
        )
        assert cleared.json()["tab_title_template"] is None


async def test_search_filters(client_for) -> None:
    t = await make_tenant("search")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        await c.post("/api/v1/companies", json={"name": "Bakkerij Jansen"}, headers=headers)
        await c.post("/api/v1/companies", json={"name": "Garage Peters"}, headers=headers)
        hit = await c.get("/api/v1/companies", params={"q": "bakker"}, headers=headers)
        assert [r["name"] for r in hit.json()["items"]] == ["Bakkerij Jansen"]

        await c.post(
            "/api/v1/contacts",
            json={"first_name": "Piet", "last_name": "de Vries", "email": "piet@x.nl"},
            headers=headers,
        )
        hit = await c.get("/api/v1/contacts", params={"q": "vries"}, headers=headers)
        assert hit.json()["total"] == 1

        await c.post("/api/v1/projects", json={"name": "Website redesign"}, headers=headers)
        assert (
            await c.get("/api/v1/projects", params={"q": "redesign"}, headers=headers)
        ).json()["total"] == 1
        assert (
            await c.get("/api/v1/projects", params={"q": "nomatch"}, headers=headers)
        ).json()["total"] == 0

        await c.post("/api/v1/tasks", json={"title": "SEO audit uitvoeren"}, headers=headers)
        assert (
            await c.get("/api/v1/tasks", params={"q": "audit"}, headers=headers)
        ).json()["total"] == 1


async def test_enabled_modules_update_and_validation(client_for) -> None:
    t = await make_tenant("modtoggle")
    headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        # The hub module can never be switched off…
        assert (
            await c.patch(
                "/api/v1/meta/tenant",
                json={"enabled_modules": ["contacts", "tasks"]},
                headers=headers,
            )
        ).status_code == 422
        # …and unknown modules are rejected.
        assert (
            await c.patch(
                "/api/v1/meta/tenant",
                json={"enabled_modules": ["companies", "nonsense"]},
                headers=headers,
            )
        ).status_code == 422

        updated = await c.patch(
            "/api/v1/meta/tenant",
            json={"enabled_modules": ["companies", "tasks", "time"]},
            headers=headers,
        )
        assert updated.status_code == 200
        assert sorted(updated.json()["enabled_modules"]) == ["companies", "tasks", "time"]

        public = (await c.get("/api/v1/meta/tenant")).json()
        assert sorted(public["enabled_modules"]) == ["companies", "tasks", "time"]
