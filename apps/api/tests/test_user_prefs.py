"""Per-user preferences (``/api/v1/prefs``): shallow-merge upsert + tenant/user isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_prefs_get_empty_then_merge(client_for) -> None:
    t = await make_tenant("prefs-merge")
    async with client_for(t.host) as c:
        h = await auth_cookie(t.user)

        assert (await c.get("/api/v1/prefs", headers=h)).json()["prefs"] == {}

        r = await c.put(
            "/api/v1/prefs", json={"prefs": {"time": {"week_view": "work"}}}, headers=h
        )
        assert r.status_code == 200
        assert r.json()["prefs"]["time"]["week_view"] == "work"

        # A second write shallow-merges into the same namespace (keeps week_view).
        r2 = await c.put("/api/v1/prefs", json={"prefs": {"time": {"foo": 1}}}, headers=h)
        assert r2.json()["prefs"]["time"] == {"week_view": "work", "foo": 1}

        got = await c.get("/api/v1/prefs", headers=h)
        assert got.json()["prefs"]["time"]["week_view"] == "work"


async def test_prefs_isolated_per_tenant(client_for) -> None:
    a = await make_tenant("prefs-iso-a")
    b = await make_tenant("prefs-iso-b")
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        await ca.put(
            "/api/v1/prefs",
            json={"prefs": {"time": {"week_view": "work"}}},
            headers=await auth_cookie(a.user),
        )
        # Tenant B never sees tenant A's preferences.
        other = await cb.get("/api/v1/prefs", headers=await auth_cookie(b.user))
        assert other.json()["prefs"] == {}
