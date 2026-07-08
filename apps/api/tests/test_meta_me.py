"""Personal account endpoint (`/meta/me`): locale preference read/write + isolation."""

from __future__ import annotations

from tests.conftest import auth_cookie, make_tenant


async def test_me_locale_defaults_null_then_updates(client_for) -> None:
    t = await make_tenant("acct")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)

        me = await c.get("/api/v1/meta/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["locale"] is None

        patched = await c.patch("/api/v1/meta/me", json={"locale": "en"}, headers=headers)
        assert patched.status_code == 200
        assert patched.json()["locale"] == "en"

        # Persisted for subsequent requests (this is what login reads to seed the cookie).
        again = await c.get("/api/v1/meta/me", headers=headers)
        assert again.json()["locale"] == "en"


async def test_me_rejects_unsupported_locale(client_for) -> None:
    t = await make_tenant("acct2")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        bad = await c.patch("/api/v1/meta/me", json={"locale": "zz"}, headers=headers)
        assert bad.status_code == 422
        assert bad.json()["error"]["message"] == "errors.validation"


async def test_me_updates_full_name_without_clearing_locale(client_for) -> None:
    t = await make_tenant("acct3")
    async with client_for(t.host) as c:
        headers = await auth_cookie(t.user)
        await c.patch("/api/v1/meta/me", json={"locale": "nl"}, headers=headers)
        # A partial update (only full_name) must not null out the locale.
        r = await c.patch("/api/v1/meta/me", json={"full_name": "Jane Doe"}, headers=headers)
        assert r.status_code == 200
        body = r.json()
        assert body["full_name"] == "Jane Doe"
        assert body["locale"] == "nl"


async def test_me_locale_is_per_user(client_for) -> None:
    """One user's language choice never affects another tenant's user."""
    a = await make_tenant("acct-a")
    b = await make_tenant("acct-b")
    async with client_for(a.host) as ca, client_for(b.host) as cb:
        await ca.patch("/api/v1/meta/me", json={"locale": "en"}, headers=await auth_cookie(a.user))
        other = await cb.get("/api/v1/meta/me", headers=await auth_cookie(b.user))
        assert other.json()["locale"] is None
