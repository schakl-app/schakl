"""Sidebar nav preferences (#169): own row → org default → none, DashboardPref's rules."""

from __future__ import annotations

import uuid

from app.core.auth.models import User
from tests.conftest import auth_cookie, make_tenant


async def _member(client, headers, email: str) -> User:
    res = await client.post(
        "/api/v1/members/invite",
        json={"email": email, "full_name": "Member", "role": "member"},
        headers=headers,
    )
    assert res.status_code == 201, res.text
    return User(
        id=uuid.UUID(res.json()["user_id"]), email=email, hashed_password="", is_active=True
    )


async def test_nav_prefs_resolution_and_default(client_for) -> None:
    t = await make_tenant("nav-prefs")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "lid@nav.example")
        member_headers = await auth_cookie(member)

        # Nothing saved anywhere: declared positions rule.
        res = await c.get("/api/v1/nav/prefs", headers=member_headers)
        assert res.json() == {"items": None, "groups": None, "source": "none"}

        # Admins set the org default; a member may not.
        default_items = [{"key": "tasks", "hidden": False}, {"key": "time", "hidden": True}]
        assert (
            await c.put(
                "/api/v1/nav/prefs/default",
                json={"items": default_items},
                headers=member_headers,
            )
        ).status_code == 403
        assert (
            await c.put(
                "/api/v1/nav/prefs/default",
                json={"items": default_items},
                headers=owner_headers,
            )
        ).status_code == 200

        # A member without their own row inherits the default...
        res = (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()
        assert res["source"] == "default"
        assert [i["key"] for i in res["items"]] == ["tasks", "time"]

        # ...their own row wins over it...
        own = [{"key": "time", "hidden": False}]
        assert (
            await c.put("/api/v1/nav/prefs", json={"items": own}, headers=member_headers)
        ).status_code == 200
        res = (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()
        assert res["source"] == "user"
        assert res["items"] == [{"key": "time", "hidden": False, "label": None}]

        # ...and resetting falls back to the org default again.
        assert (await c.delete("/api/v1/nav/prefs", headers=member_headers)).status_code == 204
        assert (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()[
            "source"
        ] == "default"


async def test_nav_labels_roundtrip_and_merge(client_for) -> None:
    """Org labels (#169) ride the default row and merge onto a member's personal layout."""
    t = await make_tenant("nav-labels")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        member = await _member(c, owner_headers, "lid@navlabels.example")
        member_headers = await auth_cookie(member)

        # The admin names an item and a group in the org's own words.
        res = await c.put(
            "/api/v1/nav/prefs/default",
            json={
                "items": [
                    {"key": "companies", "hidden": False, "label": {"nl": "Relaties"}},
                    {"key": "time", "hidden": False},
                ],
                "groups": [{"key": "assets", "label": {"nl": "Hosting", "en": "Hosting"}}],
            },
            headers=owner_headers,
        )
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["source"] == "default"
        assert body["items"][0] == {
            "key": "companies",
            "hidden": False,
            "label": {"nl": "Relaties"},
        }
        assert body["groups"] == [{"key": "assets", "label": {"nl": "Hosting", "en": "Hosting"}}]

        # A member inheriting the default sees the labels + groups.
        res = (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()
        assert res["source"] == "default"
        assert res["items"][0]["label"] == {"nl": "Relaties"}
        assert res["groups"] == [{"key": "assets", "label": {"nl": "Hosting", "en": "Hosting"}}]

        # A member's own layout carries no labels of its own, yet GET merges the org's labels
        # (and groups) back on by key — renaming stays org config.
        assert (
            await c.put(
                "/api/v1/nav/prefs",
                json={"items": [{"key": "companies", "hidden": False, "label": {"nl": "Mijn"}}]},
                headers=member_headers,
            )
        ).status_code == 200
        res = (await c.get("/api/v1/nav/prefs", headers=member_headers)).json()
        assert res["source"] == "user"
        # The personal label was ignored; the org label shows through.
        assert res["items"] == [{"key": "companies", "hidden": False, "label": {"nl": "Relaties"}}]
        assert res["groups"] == [{"key": "assets", "label": {"nl": "Hosting", "en": "Hosting"}}]


def test_nav_legacy_list_still_parses() -> None:
    """A pre-#169 row stored as a plain list reads as items-only, no error (no {items,groups})."""
    from app.core.models import NavPref
    from app.core.nav import _parse

    legacy = NavPref(items=[{"key": "tasks", "hidden": True}])
    items, groups = _parse(legacy)
    assert [(i.key, i.hidden, i.label) for i in items] == [("tasks", True, None)]
    assert groups == []

    # The new shape round-trips items, groups and labels.
    modern = NavPref(
        items={
            "items": [{"key": "companies", "hidden": False, "label": {"nl": "Relaties"}}],
            "groups": [{"key": "assets", "label": {"nl": "Hosting"}}],
        }
    )
    items, groups = _parse(modern)
    assert items[0].label == {"nl": "Relaties"}
    assert groups[0].key == "assets" and groups[0].label == {"nl": "Hosting"}


async def test_nav_label_rejects_unknown_locale(client_for) -> None:
    t = await make_tenant("nav-badlocale")
    owner_headers = await auth_cookie(t.user)
    async with client_for(t.host) as c:
        res = await c.put(
            "/api/v1/nav/prefs/default",
            json={"items": [{"key": "time", "label": {"de": "Zeit"}}]},
            headers=owner_headers,
        )
        assert res.status_code == 422, res.text


async def test_nav_default_is_tenant_isolated(client_for) -> None:
    a = await make_tenant("nav-iso-a")
    b = await make_tenant("nav-iso-b")
    a_headers = await auth_cookie(a.user)
    b_headers = await auth_cookie(b.user)
    async with client_for(a.host) as ca:
        assert (
            await ca.put(
                "/api/v1/nav/prefs/default",
                json={"items": [{"key": "tasks", "hidden": True}]},
                headers=a_headers,
            )
        ).status_code == 200
    async with client_for(b.host) as cb:
        assert (await cb.get("/api/v1/nav/prefs", headers=b_headers)).json() == {
            "items": None,
            "groups": None,
            "source": "none",
        }
